# Spec: FORMAT WITH AI for the spinner's entries

The owner (2026-10-28): paste "like 150 names of people, but either they're
all on one line, or there's a comma in between them, or there is a new line,
but not all the new lines, or it's just some weird formatting" — a button
that tidies it so "every piece of data sits on a new line".

## Behaviour

| When | Behaviour |
|---|---|
| Click FORMAT WITH AI | The entries box's text goes to OpenRouter (the key and model Fill with AI uses). The reply replaces the box: one entry per line, in the original order. |
| Nothing invented | The model is told to keep every item exactly as written and only fix separators. The server then drops any returned item that isn't in the pasted text (case and spacing aside) and says how many it dropped. |
| More than 100 | All entries are kept — it's the volunteer's data — and the status says how many to remove; the wheel's own "at most 100" error still blocks Export. |
| Over 40 characters | Kept; the wheel's own 40-character error points at it. |
| Undo | An UNDO link puts the pasted text back, until the box is edited again. |
| No key | The server's own "Add your OpenRouter API key first (in the AI panel)." |
| Empty box | "Paste some entries first." (no request) |

## API

`POST /api/ai/format` `{"text": "..."}` → `{"entries": [...], "dropped": n}`.
Errors (400, `{"error"}`): not text / blank → `Paste some entries first.`;
over 15,000 characters → `That's a lot to tidy at once — paste up to 15,000
characters.` (was 6,000 against a 4,000-token reply; the owner's 300-name
list overran it, so the reply now gets 12,000 tokens); a reply whose items
cover under 85 % of the pasted letters (cut short by the cap) → `The AI only
got through part of your list, so nothing was changed — try again, or tidy
it in two halves.`; the AI returned nothing usable → `The AI couldn't sort that
out — try again, or tidy a part of it by hand.`; plus aiassist's own key /
network messages.

Analytics: a new `ai_format` event, no props (never the text).

## UI

A **✦ TIDY MY LIST** button beside FILL WITH AI (first built as "FORMAT WITH
AI"; a UX check found FORMAT and FILL read as the same job). Under the box
a status line: "Tidying — sending your list to OpenRouter…" (the disclosure
on screen, not only in the tooltip), "Tidied into 37 entries." (neutral, not
red), "Tidied into 150 entries — the wheel holds 100, so remove 50 before
exporting." (amber), "Left out 1 entry the AI added that wasn't in your
text." / "Left out 2 entries …", and after UNDO "Undone — your text is back
as you pasted it." No key: "Add your OpenRouter key first — click FILL WITH
AI and paste it there." Tooltip: "Sends this text to OpenRouter to sort it
into one entry per line."

## Files

`aiassist.py`, `routes/ai.py`, `stats.py` (event), `static/index.html`,
`static/js/spinner.js`, `static/style.css` if needed, `scripts/smoke.py`.

## Do not

Send the text to analytics; truncate or dedupe entries; change how the
wheel splits or validates entries.

## Done means

Smoke: the guard drops invented items and keeps real ones (faked
OpenRouter), the route's errors, the 100+ case returns everything.
Browser: a messy paste becomes one entry per line, UNDO restores it.
