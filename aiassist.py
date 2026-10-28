"""AI entry generation for the spinner wheel, via OpenRouter.

The user pastes their OWN OpenRouter API key (openrouter.ai/keys). It is
stored locally in ~/.service-visuals/config.json (chmod 600) — never in the
repo, never sent to the browser (the status endpoint returns only a boolean).
The Flask server holds the key and calls OpenRouter server-side, so the key
never leaves the machine except to OpenRouter itself.

Model: defaults to `openrouter/auto` held to its LOW cost tier, which routes
to the cheapest capable model. A list of short strings is about the easiest
job an LLM gets, and the free pool behind `openrouter/free` was slow: its
models queue, and several reason at length before answering. Auto costs a
fraction of a cent per fill, so it needs credit on the account. Without
credit (OpenRouter answers 402), or if any chosen model stalls or is
unavailable, we fall back to `openrouter/free` automatically, so a key with
no credit still works the way it always did. The user can still pick
`openrouter/free` or a specific model.

No third-party dependency: the HTTP call uses urllib, like the update checker.
"""

import json
import os
import re
import socket
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import netutil

DEFAULT_MODEL = "openrouter/auto"
# The last resort every other choice falls back to: needs no credit.
FALLBACK_MODEL = "openrouter/free"
# OpenRouter's auto router ranks candidates by a cost tier; "low" "favors
# the cheapest capable models". It is also the router's default today, but
# sent explicitly so a change on their side can't quietly move a volunteer
# onto expensive models.
AUTO_ROUTER_PLUGIN = {"id": "auto-router", "cost_tier": "low"}
# Suggestions offered in the UI dropdown (the user can also type a custom slug).
PRESET_MODELS = [
    "openrouter/auto",
    "openrouter/free",
    "openai/gpt-oss-120b:free",
    "openai/gpt-oss-20b:free",
]
ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
KEY_ENDPOINT = "https://openrouter.ai/api/v1/key"
TIMEOUT = 90                    # free models can queue for a while
MAX_ENTRIES = 100
MAX_ENTRY_LEN = 40

MODEL_RE = re.compile(r"[A-Za-z0-9._/:-]{1,80}")

# Config location (holds the key + chosen model). Overridable via
# SERVICE_VISUALS_CONFIG so it can be relocated or isolated for testing.
CONFIG_DIR = os.environ.get("SERVICE_VISUALS_CONFIG") or \
    os.path.join(os.path.expanduser("~"), ".service-visuals")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")


class AiError(Exception):
    """Carries a plain-English message safe to show the user."""


# ---------------------------------------------------------------- config

def _read_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_config(config):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f)
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except OSError:
        pass


def get_key():
    """The configured key: env var wins (handy for shared setups), else the
    local config file. Returns "" when none is set."""
    env = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if env:
        return env
    return str(_read_config().get("openrouter_key", "")).strip()


def has_key():
    return bool(get_key())


def _clean_model(model):
    model = str(model or "").strip()
    return model if MODEL_RE.fullmatch(model) else ""


def get_model():
    env = os.environ.get("OPENROUTER_MODEL", "").strip()
    if env:
        return _clean_model(env) or DEFAULT_MODEL
    return _clean_model(_read_config().get("openrouter_model", "")) or DEFAULT_MODEL


def save_settings(key=None, model=None):
    """Update the stored key and/or model. Only non-None fields are touched."""
    config = _read_config()
    if key is not None:
        key = str(key).strip()
        if key:
            if len(key) > 300:
                raise AiError("That key looks too long to be valid.")
            config["openrouter_key"] = key
    if model is not None:
        clean = _clean_model(model)
        if not clean:
            raise AiError("Choose a valid model name.")
        config["openrouter_model"] = clean
    _write_config(config)


# ---------------------------------------------------------------- generation

def test_key():
    """Check the stored key against OpenRouter WITHOUT running a generation.

    Cheap and instant (no inference, no quota), so the UI can answer the two
    questions that actually matter: is my key saved and accepted, and can this
    machine reach OpenRouter at all? Returns a short status string."""
    key = get_key()
    if not key:
        raise AiError("No key is saved yet — paste your OpenRouter key above.")

    req = urllib.request.Request(KEY_ENDPOINT, headers={
        "Authorization": "Bearer " + key,
        "X-Title": "Service Visuals",
    })
    try:
        with netutil.urlopen(req, timeout=20) as resp:
            json.load(resp)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise AiError("OpenRouter rejected that key — paste it again.")
        if exc.code == 402:
            raise AiError("Key is valid, but the account is out of credit.")
        raise AiError("OpenRouter returned an error ({0}).".format(exc.code))
    except (socket.timeout, TimeoutError):
        raise AiError("OpenRouter timed out — try again in a moment.")
    except urllib.error.URLError:
        raise AiError("Couldn't reach OpenRouter — check your connection.")
    except ValueError:
        raise AiError("OpenRouter sent back something unreadable.")
    return "Key works — connected to OpenRouter."


def _parse_entries(text):
    """Pull a clean list of short strings out of the model's reply, whether it
    returned a JSON array or a plain/bulleted list."""
    text = (text or "").strip()
    items = []

    match = re.search(r"\[.*\]", text, re.S)
    if match:
        try:
            arr = json.loads(match.group(0))
            if isinstance(arr, list):
                items = [str(x) for x in arr]
        except ValueError:
            items = []

    if not items:
        # Truncated or malformed JSON array (e.g. max_tokens cut the reply
        # mid-list, leaving no closing bracket): salvage every COMPLETE quoted
        # string after the first '['. Without this, the line-split fallback
        # below used to swallow the whole one-line array as a single garbage
        # entry like '["Genesis", "Exodus", "Levi…'.
        bracket = text.find("[")
        if bracket != -1 and '"' in text[bracket:]:
            items = re.findall(r'"((?:[^"\\]|\\.){1,80})"', text[bracket:])

    if not items:                       # fallback: one entry per line
        for line in text.splitlines():
            line = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", line).strip()
            line = line.strip('"').strip("'").strip()
            if line:
                items.append(line)

    clean = []
    for item in items:
        item = " ".join(str(item).split())[:MAX_ENTRY_LEN].strip()
        if item and item.lower() not in {c.lower() for c in clean}:
            clean.append(item)
    return clean


class _Retry(Exception):
    """Internal: the current model failed in a way worth retrying on the
    fallback router. Carries the user-facing message to use if the retry
    also fails."""
    def __init__(self, message):
        super().__init__(message)
        self.message = message


def _chat(key, model, messages, temperature=0.8, max_tokens=4000,
          cost_tier="low", timeout=TIMEOUT, meta=None):
    """One OpenRouter call. Returns the assistant content string. Raises
    AiError (fatal) or _Retry (try the fallback model). `cost_tier` None
    leaves the auto router to its own (paid) choice; a `meta` dict gets
    the model that answered."""
    payload = {
        "model": model,
        "messages": messages,
        # 0.8 for inventing entries (variety); format_entries passes 0,
        # because tidying someone's list must not get creative.
        "temperature": temperature,
        # Generous: a full 100-item list is ~1k tokens of JSON, and some free
        # models spend tokens on reasoning first. 800 used to truncate long
        # lists mid-array, which corrupted the parse.
        "max_tokens": max_tokens,
    }
    # No "reasoning" effort setting: OpenRouter documents that a model
    # without reasoning can reject it with a 400, which would cost a
    # wasted round trip and a fallback on every fill. The low cost tier
    # already steers auto toward small, quick models.
    if model == DEFAULT_MODEL and cost_tier:
        payload["plugins"] = [dict(AUTO_ROUTER_PLUGIN, cost_tier=cost_tier)]
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        ENDPOINT, data=body, method="POST",
        headers={
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json",
            "X-Title": "Service Visuals",
            "HTTP-Referer": "https://github.com/jimhoggey/ServiceVisuals-App",
        })
    try:
        with netutil.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise AiError("OpenRouter rejected the key — check it in the AI panel.")
        if exc.code == 402:
            # No credit is the normal state for a free key, now that the
            # default is a paid router -- so it falls back to the free pool
            # rather than failing. Only the free pool itself refusing is
            # final, and then the message is the true reason.
            if model == FALLBACK_MODEL:
                raise AiError(
                    "That OpenRouter account is out of credit for this model.")
            raise _Retry(
                "That OpenRouter account is out of credit for this model.")
        if exc.code in (400, 404):
            raise _Retry("The model \"{0}\" isn't available right now.".format(model))
        if exc.code == 429:
            raise _Retry("OpenRouter is rate-limited right now.")
        raise _Retry("OpenRouter returned an error ({0}).".format(exc.code))
    except (socket.timeout, TimeoutError):
        raise _Retry(
            "The model took too long — free models can be busy. "
            "Try again, or use openrouter/free.")
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", "")
        if isinstance(reason, (socket.timeout, TimeoutError)):
            raise _Retry("The model took too long — free models can be busy.")
        raise AiError("Couldn't reach OpenRouter — check your connection.")
    except ValueError:
        raise _Retry("OpenRouter sent back something unreadable.")

    if meta is not None and isinstance(data, dict):
        # The model the router actually picked, e.g. "openai/gpt-6-luna".
        meta["model"] = str(data.get("model") or model)
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise _Retry("The AI didn't return any entries.")


def generate_entries(description, count, existing, model=None, full=False):
    """Ask the chosen model for spinner entries matching `description`,
    avoiding anything in `existing`. When `full` is True, return the complete
    natural set (e.g. every book of the Bible) up to MAX_ENTRIES; otherwise
    return exactly `count`. Falls back to openrouter/free if the chosen model
    stalls, is unavailable, or needs credit the account doesn't have. Raises
    AiError with a friendly message on failure."""
    key = get_key()
    if not key:
        raise AiError("Add your OpenRouter API key first (in the AI panel).")

    count = max(1, min(MAX_ENTRIES, int(count)))
    existing = [str(e).strip() for e in (existing or []) if str(e).strip()]
    avoid = ", ".join(existing[:60]) if existing else "(none)"

    chosen = _clean_model(model) or get_model()
    # Try the chosen model, then the free pool (unless it IS the free pool).
    chain = [chosen] if chosen == FALLBACK_MODEL else [chosen, FALLBACK_MODEL]

    if full:
        cap = MAX_ENTRIES
        ask = (
            "List the COMPLETE set for: {desc}.\n"
            "If it's a well-known finite set (e.g. books of the Bible, months "
            "of the year, countries in a region), return every item in the "
            "natural order — do not stop short and do not pad. Otherwise return "
            "a good variety. Return at most {cap} items."
        ).format(desc=str(description).strip(), cap=MAX_ENTRIES)
    else:
        cap = count
        ask = (
            "Give me exactly {n} entries for: {desc}."
        ).format(n=count, desc=str(description).strip())

    messages = [
        {"role": "system", "content": (
            "You generate entries for a spinner/wheel picker. "
            "Return ONLY a JSON array of short strings — no prose, no numbering, "
            "no markdown. Each entry is at most 40 characters. No duplicates.")},
        {"role": "user", "content": (
            ask + "\nDo not repeat any of these existing entries: {avoid}."
        ).format(avoid=avoid)},
    ]

    have = {e.lower() for e in existing}
    last_message = "The AI didn't return a usable list — try rewording."
    for attempt_model in chain:
        try:
            content = _chat(key, attempt_model, messages)
        except _Retry as retry:
            last_message = retry.message
            continue                     # try the fallback model
        entries = _parse_entries(content)
        entries = [e for e in entries if e.lower() not in have]
        if entries:
            return entries[:cap]
        last_message = "The AI didn't return a usable list — try rewording."

    raise AiError(last_message)


# ---------------------------------------------------------------- format
# FORMAT WITH AI (docs/specs/spinner-format.md): a messy paste -- 150 names
# on one line, commas here and new lines there -- sorted into one entry per
# line. Tidying, not writing: the model may only re-separate what is there.

# 15,000 characters covers a 300-name list (the wheel's maximum) with room.
FORMAT_MAX_CHARS = 15000
# The owner's 300 names sat on "Tidying…" for 3 minutes: in one request
# the low-cost router picked a reasoning model that spent all 12,000
# tokens thinking and returned nothing, then the free pool queued. The
# reply is the whole list again, so its length is the wait -- split the
# paste into pieces of about 60 names and tidy them side by side. A
# 300-name list now takes about as long as one piece (~5-10 s).
FORMAT_CHUNK_CHARS = 1200
FORMAT_WORKERS = 8
FORMAT_MAX_TOKENS = 6000      # per piece: ~60 names, plus reasoning room
FORMAT_TIMEOUT = 60           # per piece, so a stall is seconds not minutes
# Tidying runs on the auto router's own choice, not the low cost tier the
# fills use: the owner has a paid key, and the low tier's pick is what
# stalled. A piece costs a fraction of a cent.
FORMAT_COST_TIER = None
# A reply cut short by the token cap still parses -- the salvage in
# _format_items keeps every complete item -- and would silently lose the
# last names. If the kept items cover less than this share of the
# pasted text's letters, it's an error, not a quiet loss.
# Letters only, so numbering ("1.", "2)") the AI rightly drops doesn't
# count against it; 0.85 leaves room for a dropped heading like "Names:".
FORMAT_MIN_COVERAGE = 0.85
ERR_FORMAT_EMPTY = "Paste some entries first."
ERR_FORMAT_LONG = ("That's a lot to tidy at once — paste up to 15,000 "
                   "characters.")
ERR_FORMAT_PARTIAL = ("The AI only got through part of your list, so "
                      "nothing was changed — try again, or tidy it in two "
                      "halves.")
ERR_FORMAT_NOTHING = ("The AI couldn't sort that out — try again, or tidy a "
                      "part of it by hand.")
ERR_FORMAT_NO_KEY = ("Add your OpenRouter key first — click FILL WITH AI "
                     "and paste it there.")


# "1. ", "12) " or "3 - " at the start of a line: list numbering.
_NUMBERING_RE = re.compile(r"(?m)^\s*\d{1,3}\s*[.)\-:]\s+")


def _squash(text):
    """Lower-case, every run of spaces/separators to one space: the form in
    which a returned item is looked for in the pasted text."""
    return " ".join(re.sub(r"[\s,;|/\t]+", " ", str(text)).lower().split())


def _format_items(content):
    """The reply's list, full length and in order -- unlike _parse_entries,
    nothing is truncated to 40 characters or de-duplicated: two people can
    share a name, and a too-long entry is the wheel's error to show."""
    text = (content or "").strip()
    match = re.search(r"\[.*\]", text, re.S)
    items = []
    if match:
        try:
            arr = json.loads(match.group(0))
            if isinstance(arr, list):
                items = [str(x) for x in arr]
        except ValueError:
            items = []
    if not items:
        bracket = text.find("[")
        if bracket != -1:
            items = re.findall(r'"((?:[^"\\]|\\.)+)"', text[bracket:])
    return [" ".join(i.split()) for i in items if i.strip()]


def format_entries(text, model=None):
    """Sort pasted `text` into separate entries. Returns (entries, dropped):
    `dropped` counts items the model returned that are not in the text --
    a model asked to tidy a list of names can still "helpfully" add or
    correct one, and a church's list must come back as it went in.
    Raises AiError with a plain-English message."""
    if not isinstance(text, str) or not text.strip():
        raise AiError(ERR_FORMAT_EMPTY)
    if len(text) > FORMAT_MAX_CHARS:
        raise AiError(ERR_FORMAT_LONG)
    key = get_key()
    if not key:
        # Says where the key goes: the button sits beside FILL WITH AI,
        # whose panel holds it, and "the AI panel" didn't say so.
        raise AiError(ERR_FORMAT_NO_KEY)
    chosen = _clean_model(model) or get_model()
    # The auto router picks afresh each call, so a piece it fumbled (cut
    # short, numbers dropped) gets a second go there before the slower
    # free pool -- one bad piece otherwise fails the whole tidy.
    chain = [chosen] if chosen == FALLBACK_MODEL else [chosen, FALLBACK_MODEL]
    if chosen == DEFAULT_MODEL:
        chain.insert(1, DEFAULT_MODEL)
    pieces = _format_chunks(text)
    if len(pieces) == 1:
        results = [_format_piece(key, chain, pieces[0])]
    else:
        with ThreadPoolExecutor(
                max_workers=min(FORMAT_WORKERS, len(pieces))) as pool:
            results = list(pool.map(
                lambda piece: _format_piece(key, chain, piece), pieces))
    failed = [r for r in results if isinstance(r, AiError)]
    if failed:
        # One piece failing fails the lot: half a tidied list pasted over
        # the original would lose the other half's names.
        raise failed[0]
    kept = [item for r in results for item in r[0]]
    # The owner asked to see which model did the tidy ("auto" picks one
    # per call, so the pieces of a long list can differ).
    models = []
    for r in results:
        if r[2] not in models:
            models.append(r[2])
    return kept, sum(r[1] for r in results), ", ".join(models)


def _format_chunks(text):
    """Split `text` into pieces of about FORMAT_CHUNK_CHARS, cut only just
    after a separator -- a new line first, then , ; |, then a colon, and
    only failing all of those a space -- so no name is cut in two."""
    pieces = []
    start = 0
    while len(text) - start > FORMAT_CHUNK_CHARS:
        window = text[start:start + FORMAT_CHUNK_CHARS]
        cut = -1
        for seps in ("\n", ",;|\t", ":", " "):
            cut = max(window.rfind(s) for s in seps)
            if cut > FORMAT_CHUNK_CHARS // 2:
                break
        if cut <= 0:
            cut = len(window) - 1
        pieces.append(text[start:start + cut + 1])
        start += cut + 1
    pieces.append(text[start:])
    return [p for p in pieces if p.strip()]


def _format_piece(key, chain, text):
    """Tidy one piece. Returns (kept, dropped), or the AiError to raise
    (returned, not raised, so every parallel piece gets to finish)."""
    messages = [
        {"role": "system", "content": (
            "You split messy pasted text into the separate items of a list "
            "for a spinner wheel. Return ONLY a JSON array of strings, in "
            "the original order -- no prose, no numbering, no markdown. "
            "Keep every item exactly as written: fix only the separators "
            "and the spaces around items. Do not add, remove, merge, "
            "correct, translate, sort or invent anything. A person's full "
            "name (first and last name) is ONE item. Keep numbers, "
            "initials and brackets that are part of an item.")},
        {"role": "user", "content": text},
    ]
    haystack = _squash(text)
    letters = sum(ch.isalpha() for ch in text) or 1
    # Digits are checked on their own: a model once returned 72 of 300
    # "Sophie Hassan 0"-style entries without their numbers, and letters
    # alone couldn't see it. List numbering ("1.", "2)") is the model's
    # to drop, so it doesn't count.
    digits = sum(ch.isdigit() for ch in _NUMBERING_RE.sub(" ", text))
    last_message = ERR_FORMAT_NOTHING
    for attempt_model in chain:
        meta = {}
        try:
            content = _chat(key, attempt_model, messages, temperature=0,
                            max_tokens=FORMAT_MAX_TOKENS,
                            cost_tier=FORMAT_COST_TIER,
                            timeout=FORMAT_TIMEOUT, meta=meta)
        except AiError as exc:
            return exc
        except _Retry as retry:
            last_message = retry.message
            continue
        items = _format_items(content)
        kept = [i for i in items if _squash(i) and _squash(i) in haystack]
        if not kept:
            last_message = ERR_FORMAT_NOTHING
            continue
        covered = sum(ch.isalpha() for item in kept for ch in item)
        kept_digits = sum(ch.isdigit() for item in kept
                          for ch in _NUMBERING_RE.sub(" ", item))
        if (covered < FORMAT_MIN_COVERAGE * letters or
                kept_digits < FORMAT_MIN_COVERAGE * digits):
            # Cut short (the token cap, a model that gave up): returning it
            # would quietly delete the rest of someone's list.
            last_message = ERR_FORMAT_PARTIAL
            continue
        return (kept, len(items) - len(kept),
                meta.get("model") or attempt_model)
    return AiError(last_message)
