# Spec: Full-size and freeze-until-the-end milliseconds (countdown)

**Status:** proposed, not yet built (would ship after v1.32.0). **Owner:**
orchestrator. **Implementers:** three Sonnet agents (renderer, frontend +
preview, validation + smoke), one reviewer.

**Revision note:** this replaces an earlier draft built around an "anchor
rule" (main digits pinned at their plain size, millis appended to the
right, refused on several style/option combinations because the overflow
math never closed). The owner proposed a simpler design that removes the
problem at the root instead of working around it. This draft supersedes
the anchor rule entirely — see "The design" below.

## Why

Milliseconds on a countdown (v1.23.0) always draw at 55% size, for the
whole clip. The owner wants two more knobs, independent of each other:

  (a) draw the milliseconds at the **same size** as the main digits, and
  (b) have milliseconds **read zero until the final stretch** of a long
      countdown, then start ticking — a 15-minute timer shows `15:00.000`
      … `1:00.000` (frozen), then the `.mmm` run starts moving for the
      last minute.

The owner's overriding, twice-stated requirement for (b): the transition
must be **seamless** — the main digits must not jump in size or position
when the milliseconds start ticking.

## The design (replaces the anchor rule)

The milliseconds run is **always on screen, for the entire render**,
whenever `show_millis` is on — with or without `millis_reveal`. Before the
threshold it reads `.000`; at and after it, it reads the live value. That
is the whole behavioural change. There is no moment where the millis run
appears, disappears, or resizes, because it never does either — layout is
computed once, exactly the way today's existing `show_millis=True` path
already computes it, and is never touched again for the life of the
render. Seamlessness is not enforced by careful positioning any more —
it's structurally impossible to violate, because nothing about the layout
is a function of time or of `millis_reveal` at all. This is a strictly
simpler, strictly stronger guarantee than the anchor rule's, and it is why
every refusal in the previous draft is gone (see "Collision check" below).

**On the frozen `.000` being honest, not a lie:** it is not a claim that
zero milliseconds have elapsed within whatever second is currently
showing — it's a fixed placeholder meaning "this timer isn't tracking
milliseconds yet." It is exactly correct at one instant in every displayed
second (the tick-over boundary) and a deliberate simplification for the
other ~29 frames of that second. Stated plainly, because it must not be
mistaken for a bug: **between the start and the threshold, the
milliseconds digits do not reflect real elapsed sub-second time at all —
they are a fixed placeholder, not a live value that happens to move
slowly.** At and after the threshold they become the real, live value,
exactly as `show_millis` already renders it today.

Countdown only. Clock mode keeps its own display rules untouched, the same
way `fixed_format` was countdown-only (docs/specs/clock-mode.md addendum,
v1.31.0) — every example the owner gave is a countdown, and "near the end"
has no equivalent for a clock that just ticks forward forever.

## Investigation already on record (cited, not re-derived)

Established fact from prior investigation of `render/timer.py` /
`static/js/timer.js` / `validation.py`:

- Entry point `render_timer()` `render/timer.py:770-942`; the millis
  branch in `make_frame` around `:899-920` — this branch, **unmodified**,
  is exactly what this spec reuses for the "ticking" (live) stretch.
- Text split: main = `_format_remaining(rem_ms // 1000, total, fixed)`
  (`:908`); `ms_text = ".{0:03d}".format(rem_ms % 1000)` (`:913`).
  `_format_remaining` at `:446-468`.
- `CLOCK_MS_SCALE = 0.55` at `:499`, applied at `:856-857` to build a
  separate `met_ms` via `_digits_metrics`. Shared with clock mode
  (`_clock_font_size` `:551-581`, `_render_clock` `:698`).
- Size is auto-fit from string width, computed **once per render**, before
  frame 0, from `initial_text = _format_remaining(total, total, fixed)`
  (`:832`) — with `show_millis` on, this already includes the `.000` (or
  live) run in the fit (`:839-849`). This spec does not change *when* that
  fit runs — it already runs unconditionally whenever `show_millis` is
  on — only *what ratio* it uses (see Full-size millis, below).
- Centring: the composite block (main + millis, and a tag in clock mode)
  is centred as **one unit** — `_paste_digits(base, block, WIDTH//2 -
  block.width//2, ...)` (`:887-889`, `:916-919`). This spec keeps that
  centring exactly as-is, for the *entire* render, in *every* frame —
  there is no second positioning rule to add.
- FPS: `show_millis` forces `fps=out_fps=30` (`:806-811`); the encoder
  (`render/encoder.py:69-98,161-198`, `-r` at `:188`) takes one input fps
  for the whole ffmpeg pipe — a two-phase fps within one render is not
  possible.
- `MILLIS_MAX_SECONDS = 1800` in `validation.py:45`, enforced `:138-141`.
  `static/js/timer.js:400` mirrors it as a bare literal with a **stale**
  comment claiming it lives in `app.py` — it now lives in `validation.py`.
  Fixed as part of this work (frontend agent, below).
- `_clock_font_size`'s only callers are `_render_clock` (`:691,695`) and
  `render_timer`'s own millis refit (`:848,851,854`) — confirmed by
  `grep -rn _clock_font_size`, nothing outside `render/timer.py` calls it.
  `_render_clock_block` (`:717,914`) already takes `met_main`/`met_ms` as
  independent objects and already draws whatever `ms_text` it's given —
  it needs **no change** to draw a frozen `.000` instead of a live one.
  `_paste_digits` (4 call sites, all inside `render/timer.py`) is a plain
  positional paste; it needs no change either.
- `base_for` (`:876-891`, the existing non-millis per-second cache) is a
  **separate, sibling closure** to `_render_clock`'s own `base_for`
  (`:721`) inside `_render_clock` — the codebase already duplicates this
  pattern rather than sharing one function across the two render paths.
  This spec adds a third sibling (below) rather than overloading either.
- JS preview duplicates the sizing math by hand and must change in
  lockstep: `clockCompositeWidth()` `static/js/timer.js:564-576`
  (hardcodes `0.55`/`0.28`), `drawClockComposite()` `:583-630`, per-style
  fit blocks at `:660-661, :708-709, :716-720, :666-667, :735-736`.
- The preview only ever renders **frame 0** (`:632-634, :686-689`) —
  millis there are always exactly `.000`, never wall time. Under this
  design that is simply correct for the frozen state too (see Preview).
- `golden.py` already has two millis jobs, `timer/classic-millis`
  (`:198-201`) and `clock/classic-12h-millis` (`:216-220`); it hashes
  **decoded pixels** (`_hash_mp4`, `:76-95`), so any pixel change to
  today's `show_millis=True` output fails `--check` until `--record` runs.
- `scripts/smoke.py`'s `check_countdown_millis_format()` (`:509-524`) is a
  pure-function check; the real millis renders at `:2131-2139` (countdown)
  and `:2169-2174` (clock) go through `verify()`, which checks
  container/duration only, never pixels.
- CLAUDE.md's byte-identical guard covers the **plain** (non-millis)
  countdown path only, which this feature does not touch at all (see Files
  & ownership) — but `render/timer.py`'s countdown branch is still edited,
  so the guard's proof procedure still applies (see Done means).

## Full-size millis (option a — independent of the freeze)

`_millis_size(main_size, full_size)` (new, pure): returns `main_size` when
`full_size` is true, else `max(1, int(round(main_size * CLOCK_MS_SCALE)))`
— i.e. exactly today's ratio when off. This is the one function that
decides the millis font size everywhere; nothing computes `size * 0.55`
inline any more. `_clock_font_size` gains one new optional parameter,
`ms_scale=CLOCK_MS_SCALE` (defaults to today's constant, so all 6 existing
call sites — clock mode ×2, countdown's millis refit ×3 (ring, bar,
classic) — are untouched unless they explicitly opt in), used at the
*existing, unconditional* refit call: `ms_scale=(1.0 if millis_full_size
else CLOCK_MS_SCALE)`. That one substitution is the entire sizing change
this spec makes — `millis_reveal` never touches sizing at all (see below).

### What full-size millis cost in digit size

The joint auto-fit has to hold a much wider string, so turning this
on makes the main digits **smaller**. This is the option's real,
visible cost and the operator should not discover it by surprise —
measured with the renderer's own metrics:

| style | text | 55% (today) | 100% | change |
|---|---|---|---|---|
| classic | `15:00` | 400px | 362px | -10% |
| classic | `00:15:00` | 321px | 274px | -15% |
| bar | `15:00` | 330px | 330px | 0% (already at cap) |
| bar | `00:15:00` | 328px | 281px | -14% |
| ring | `15:00` | 190px | 159px | -16% |
| ring | `00:15:00` | 141px | 119px | -16% |

Nothing collides (see the collision check below) — the digits just
get smaller to make room. The hint text under the option must say so
in plain words, e.g. "Milliseconds the same size as the numbers —
the whole time is drawn a little smaller to fit." Ring is the
tightest of the three, and `fixed_format` costs more than a plain
total on every style because `HH:MM:SS` is already the widest shape.

## Collision check — re-measured for the new design, nothing refused

Because layout no longer depends on whether the millis are frozen or
ticking, the only question left is the one the *existing*, already-shipped
`show_millis=True` path already answers for 55%: does the joint auto-fit
(`_clock_font_size`) keep the combined main+millis string inside its
target width at 100% too? I measured this directly (the renderer's own
`_digits_metrics`/`_text_width`, run against every text shape a countdown
can produce — `M:SS`, `MM:SS`, `HH:MM:SS` under `fixed_format` — not
estimated), computing both the fitted font size *and* the actual rendered
width at that size, for 55% and 100%, on all three styles:

| style (fit target) | worst-case margin, non-fixed (at 100%) | worst-case margin, `fixed_format` (at 100%) |
|---|---|---|
| classic (1600px, screen half 960px) | 161px (15:00/30:00) | 162px |
| bar (1640px, screen half 960px) | 233px (15:00/30:00) | 141px |
| ring (702px, ring half 351px) | **0px** (5:00) | **-0.8px** |

Classic and bar fit with generous room in every case — nothing new to
build for them. **Ring is the tight one**, as suspected, but not because
anything overflows in a way that would be visible:

- For every non-`fixed_format` total, ring's margin at 100% is small but
  not negative — from a hair above zero (`5:00`: 0px, the shortest and
  most common total) up to 1.2px (`15:00`/`30:00`). This is not a new
  risk this feature introduces: ring's *already-shipped* 55% millis path
  runs at a comparably tight margin today for `fixed_format` (+1.0px at
  55%, both totals, already live in production). 100% is the same
  mechanism, same formula, applied at a ratio that happens to land a
  little tighter.
- For `fixed_format` at 100% specifically (both totals, since
  `fixed_format` always produces the same 8-character shape regardless of
  total), the measured margin is **-0.8px** — the ref-then-scale estimate
  `_clock_font_size` uses (measure at a reference size, scale, then clamp)
  is not perfectly linear with PIL's actual font rasterisation at small
  integer sizes, so it can overshoot its own fit target by a fraction of a
  pixel. This is real, and worth recording so nobody re-discovers it while
  eyeballing an extracted frame and mistakes it for a bug — but it is
  **35+ px inside the ring's true (harder) inner edge at 387px** (the
  351px fit-zone boundary already has a deliberate 36px safety margin
  baked into `RING_INNER_FIT`, precisely to absorb slop like this).
  Nothing visibly touches the ring.

**Conclusion: no refusal, no clamp, on any style, for any combination of
`millis_full_size` × `millis_reveal`.** `millis_reveal` in particular has
*zero* effect on sizing (it only changes which text string is drawn — see
below), so it cannot make the fit any tighter than plain `show_millis` on
its own already is today. The two rules ("full-size + freeze can't
combine", "freeze refused on ring") from the superseded draft are deleted,
not weakened — there is nothing left for them to guard against.

`fixed_format` alone, without millis, was **not** re-litigated — ring's
non-millis path already draws at a hardcoded 190px with no fit check, so
any pre-existing tightness there predates this feature and is out of
scope.

**2-digit millis, noted but not built:** `.00` instead of `.000` is
narrower and gives even more margin — measured `"15:00.00"` at the forced
400px classic cap is 1543px wide (188px margins each side), vs. `.000`'s
1765px (77px margins) at that same forced cap. (The *actual* auto-fitted
classic size for `"15:00.000"` at 100% is 362px, not the 400px cap, giving
1598px/161px margins — more conservative than the cap-forced figure
either way.) This spec defaults to 3-digit millis, today's existing
format, which needs no new formatting code. A 2-digit variant is a small,
well-contained follow-up (a different literal in the frozen text, and in
the live `"{0:02d}".format(...)`-style formatter) if the owner wants it
later — not built now.

## The freeze/tick boundary

`_millis_ticking(rem_ms, millis_reveal, millis_reveal_seconds)` (new,
pure): `return (not millis_reveal) or (rem_ms <= millis_reveal_seconds *
1000)`. Inclusive at the boundary, matching the existing warn-colour
check's own `rem_ms <= 10_000` convention (`:911-912`) rather than
inventing a new comparison style. `millis_reveal=False` always ticks —
this is exactly today's plain `show_millis` behaviour, byte-identical,
untouched.

- `not _millis_ticking(...)` (frozen): main digits still tick down
  normally, once per second; the millis run reads the fixed `.000`.
  Rendered through the new cached path below.
- `_millis_ticking(...)` (live): **exactly today's existing, unconditional
  `show_millis` branch in `make_frame`** (`:899-920`), completely
  unmodified — same `rem_ms % 1000` formatting, same warn-colour check,
  same uncached per-frame render. This is also what runs for the entire
  clip when `millis_reveal` is off.

**Degenerate case, not an error:** if `millis_reveal_seconds >= total`,
`_millis_ticking` is already `True` at frame 0 (`total*1000 <=
millis_reveal_seconds*1000`), so the render is live from the first frame —
indistinguishable from `millis_reveal` being off. No special-casing
needed; it falls out of the formula.

**Hold phase:** unaffected. `rem_ms` is clamped to 0 for the whole hold
(`max(0, ...)`, `:906`), and `0 <= millis_reveal_seconds*1000` for any
valid (≥1) threshold, so `_millis_ticking` is already `True` throughout
the hold — matching today's documented "the whole hold reads `0:00.000`"
behaviour, unmodified.

## The frozen-stretch cache — a real win, not just a note

Today, `show_millis=True` makes `make_frame`'s millis branch build a fresh
block from scratch on **every single frame**, unconditionally — even
though most of those frames, for a `millis_reveal` render, don't need to
be unique at all: the frozen stretch reads `.000` and a main string that
only changes once per second. Wiring that stretch through a cache is what
makes a long freeze render fast rather than merely correct.

New sibling closure `frozen_base_for(rem, idx)`, alongside the existing
`bases`/`base_for` (added, not merged into `base_for` — keeping it fully
separate means the existing non-millis path, and its byte-identical
guarantee, is not touched by a single line):

- New `frozen_bases = OrderedDict()`, own lock, own cap
  (`bases_cap`'s existing formula, `16 * min(4, n_plates)`, reused as-is —
  same LRU-window reasoning already documented at `:872-875`: the cap
  doesn't need to hold every distinct second of a long countdown, only
  enough to catch the reuse within each one).
- `color = accent if (warn_last10 and rem <= 10) else DIGITS_COLOR` —
  **the plain `base_for`'s existing whole-second formula**, not the live
  branch's millisecond-precision `rem_ms <= 10_000` check. This is a
  deliberate, matching choice: it's what makes every frame of one frozen
  second share one cache key, and it's arguably the more consistent
  choice anyway — the millis digits carry no real sub-second information
  during the freeze, so there's no reason for their *colour* to change at
  sub-second precision either. (In the default configuration this is
  moot: `millis_reveal_seconds` defaults to 60, far outside the 10s warn
  window, so the two never interact in practice.)
- `text = _format_remaining(rem, total, fixed)`; cache key `(text, color,
  idx)` — identical shape to `base_for`'s.
- Block: `_render_clock_block(text, ".000", "", color, color, met, met_ms,
  None, 0)` — the *existing* function, unmodified, just given a fixed
  `.000` instead of a live value. Paste: `_paste_digits(base, block,
  WIDTH//2 - block.width//2, digits_cy - block.height//2, has_bg)` — the
  *same* centring formula the live millis path already uses (`:916-919`);
  there is no separate positioning rule to write.

`make_frame`'s only new logic: when `show_millis`, compute `rem_ms` as
today, then branch on `_millis_ticking` — not-ticking calls
`frozen_base_for(rem_ms // 1000, idx)`; ticking falls through to today's
existing branch, verbatim.

**What this saves**, for a 15-minute (900s) countdown at the defaults
(`hold_seconds=5`, `millis_reveal_seconds=60`, 30fps): today's
unconditional path would render `(900+5)*30 = 27,150` frames, every one a
fresh block build. With the cache, the frozen stretch covers `900-60 =
840` distinct seconds — each built once and reused for its ~30 frames —
and the live stretch (`60s` reveal window + `5s` hold = `65s`) still
builds all `65*30 = 1,950` frames uncached, exactly as today. Total unique
block builds: `840 + 1,950 = 2,790`, against `27,150` — **roughly 90%
fewer**, for the exact same output. Without this wiring, a long
`millis_reveal` countdown would render *slower* than plain
`show_millis=True` does today for the same duration, for no visual
benefit — worth stating plainly since it's an easy thing to ship
correct-but-slow.

## Frame rate

Unchanged trigger: `show_millis=True` still forces `fps=out_fps=30` for
the *entire* render (`:806-811`) — already true before this feature, and
still can't become two-phase (the encoder takes one input fps per pipe,
`render/encoder.py:69-98`). `MILLIS_MAX_SECONDS` (1800s / 30 min) still
applies in full: a `millis_reveal` render is not exempt from it just
because most of its frames are now cached — the ceiling exists because
the *encode*, not the frame-building, is 30fps for the whole clip, and
that part is unchanged by the cache.

## Preview

**The old gap is gone.** The previous draft worried that frame 0 (the
preview's only frame) sits before the threshold and would look identical
to millis being off. Under this design that concern doesn't apply: frame
0 now legitimately, honestly shows `"15:00.000"` — the millis run is
genuinely on screen, genuinely reading zero, exactly as the real render's
first frame will. **No new preview logic is needed at all.**
`drawTimerPreview()`'s existing millis branch (`:672-750`) already always
uses `total`/`total` (frame 0's remaining time) and always draws
`millis = ".000"` when `t.showMillis` — that is now correct for
`millis_reveal` too, unconditionally, including the degenerate case
(`millis_reveal_seconds >= total`: frame 0 is still `.000` either way,
since `rem_ms % 1000 == 0` exactly at `i=0` for any whole-second total).
Nothing to change in `clockCompositeWidth()`/`drawClockComposite()` beyond
threading the `msScale` parameter through for full-size (below) — no
`anchorMainWidth` parameter, no reveal-specific branch, because the
preview's frame-0 rule already matches the renderer's frozen-frame rule by
coincidence of both being "the composite, drawn once, unchanging."

**Is a caption still warranted?** Not as a correction — nothing is being
hidden or misrepresented any more. But a plain informational hint is still
worth keeping, in the same spirit as clock mode's "Shows as 7:59:50 PM"
line: the preview is a single static frame and can't show *when* the
freeze ends, which is a real fact the operator needs before exporting. So:
keep one hint line under the seconds field, wording below — not because
the preview would otherwise mislead, just because a static image can't
convey a threshold in time on its own.

`clockCompositeWidth`/`drawClockComposite` gain one new optional
parameter, `msScale` (default `0.55`, matching today), threaded the same
way `ms_scale` is on the Python side, for full-size. That is the entire
JS sizing change.

## Behaviour (what the operator sees)

Both new controls live in the existing **Milliseconds** group inside the
`Advanced` details (`static/index.html:355-372`, fieldset
`timer-millis-group`), directly under **Show milliseconds** — they only
have a visible effect when that box is checked, but (per the API contract
below) are accepted and validated regardless of it, exactly like
`fixed_format` is today. Neither control disables or is disabled by the
other, or by style choice — there is no invalid combination left to guard
against in the UI.

| Control | Details |
|---|---|
| Full-size milliseconds | Checkbox, `timer-millis-full-size`, default off. Label: **Full-size milliseconds**. Hint: "The .000 is the same size as the minutes and seconds. The whole timer is drawn a little smaller so it still fits." (Must name the shrink — see What full-size millis cost. Never say "55%": a volunteer does not know what it is 55% of.) |
| Hold at zero until the end | Checkbox, `timer-millis-reveal`, default off. Label: **Hold at zero until the end** (renamed from "Reveal near the end": nothing is revealed — the millis are on screen the whole time, so the old name described the superseded design). Hint: "Shows .000 without ticking until the last few seconds, then counts down normally." |
| Seconds before the end | Number input, `timer-millis-reveal-seconds`, 1-1800, default **60** (owner confirmed: starts on the 1:00 boundary). Label: **Start ticking with this many seconds left**. Shown only while Hold at zero is checked. |

If the seconds field's value is ≥ the countdown's current total, its hint
instead reads: "Milliseconds tick for the whole timer (it's only Ns
long)." — not an error (see the API contract's degenerate case), just an
honest description of what will actually render.

## API contract (type stays `"timer"`, no new top-level keys)

```json
{"type": "timer", "options": {
  "minutes": 15, "seconds": 0, "style": "classic",
  "accent": "#e8b44f", "warn_last10": true, "hold_seconds": 5,
  "show_millis": true, "fixed_format": false,
  "millis_full_size": true,
  "millis_reveal": true,
  "millis_reveal_seconds": 60
}}
```

(This example deliberately sets both new options — under this design
that's an entirely ordinary, valid render, not a special case.)

Three new keys, all in **countdown** payloads only (clock-mode payloads
never see them — `_validate_clock_options` doesn't read them, same as it
already ignores `fixed_format`). Both are accepted and validated
**regardless of `show_millis`**, exactly like `fixed_format` today —
they're simply inert when millis are off. No cross-field checks between
them, or against `style` — there is nothing left to cross-check (see
Collision check, above).

- `millis_full_size` — bool, default `false`. Not a bool →
  *"Full-size milliseconds" must be true or false.*
- `millis_reveal` — bool, default `false`. Not a bool →
  *"Hold at zero until the end" must be true or false.* (The quoted name
  must match the UI label exactly.)
- `millis_reveal_seconds` — whole number, **1 to 1800**, default `60`.
  Custom message (same shape as `_clip_length_field`'s, since the generic
  `_int_field` template's phrasing doesn't fit a "seconds left" field
  naturally): *"Milliseconds can start ticking with 1 to 1800 seconds
  left on the timer."* (plus the same bool/fraction/type sub-messages
  `_clip_length_field` already has, reused verbatim). Ceiling matches
  `MILLIS_MAX_SECONDS` — a countdown with millis on can never be longer
  than 1800s anyway, so the field's own static range can't usefully
  exceed it.

**Degenerate case, not an error:** `millis_reveal_seconds >= total` —
covered under The freeze/tick boundary, above; ticks for the whole render,
no special-casing, no rejection.

**Byte-identical when off:** with both new keys at their default `false`,
`render_timer`'s countdown branch takes the exact same code path as today
(`_millis_size` returns today's `CLOCK_MS_SCALE` ratio unchanged;
`_millis_ticking` is always `True`, so `frozen_base_for` is never called).
`golden.py`'s existing `timer/classic-millis` job must still match its
recorded hash unchanged.

**Filename:** no new descriptor. Only `_ms` (already exists) and `_green`
carry filename weight today; `fixed_format` — the nearest precedent, also
a cosmetic refinement of an existing toggle — added none either.

**Analytics:** no new prop. `_timer_props` (`app.py:127-135`) sends only
`mode`, `style`, `bg` today — notably, `show_millis` itself, `fixed_format`
and `hold_seconds` are *not* surfaced there despite existing for multiple
releases. `app.py` needs **no changes at all** for this feature (it only
wires `render_timer` and `_timer_props`, neither of which this spec
touches).

## Files & ownership (agents edit ONLY their own files)

**Renderer agent** — `render/timer.py`, `README.md`.
- `_clock_font_size`: add `ms_scale=CLOCK_MS_SCALE` parameter (extend
  only — every existing call site keeps working unchanged).
- New pure function `_millis_size(main_size, full_size)`.
- New pure function `_millis_ticking(rem_ms, millis_reveal,
  millis_reveal_seconds)`.
- `render_timer`'s countdown branch (`:770-942`): read the 3 new options
  (already validated/normalised); pass `ms_scale` into the *existing,
  unconditional* millis refit call (one-line change; the refit itself
  keeps running exactly when it runs today, `if show_millis:`, regardless
  of `millis_reveal`). Add `frozen_bases`/`frozen_base_for` as a new
  sibling of `bases`/`base_for`, per "The frozen-stretch cache" above.
  Branch `make_frame`'s existing `if show_millis:` body on
  `_millis_ticking`: not-ticking → `frozen_base_for`; ticking → today's
  existing branch, byte-for-byte unchanged. Do not touch `_render_digits`,
  `_render_clock_block`, `_paste_digits`, `base_for`, or the clock-mode
  branch (`_render_clock`) — none of them need to change.
- `README.md`: one clause on the existing Timer bullet (`README.md:59-67`).

**Frontend + preview agent** — `static/index.html`, `static/js/timer.js`.
- Markup: two checkboxes + one number field inside `#timer-millis-group`
  (`static/index.html:361-370`), ids `timer-millis-full-size`,
  `timer-millis-reveal`, `timer-millis-reveal-seconds`, per Behaviour. No
  disabling logic between them or against style — just the seconds
  field's own show/hide against its checkbox.
- Fix the stale comment at `static/js/timer.js:400` (it says "Mirrors
  MILLIS_MAX_SECONDS in app.py" — that constant now lives in
  `validation.py`).
- `readTimer()`: add `millisFullSize`, `millisReveal`,
  `millisRevealSeconds`. `timerPayload()`: add the 3 keys to the countdown
  branch only (never the clock branch).
- `validateTimerDuration()` (or a new sibling): range-check
  `millis_reveal_seconds` (1-1800) with the exact message above. No other
  new validation — there are no cross-field rules to mirror any more.
- `clockCompositeWidth()`/`drawClockComposite()`: add the `msScale`
  parameter exactly as specified in Preview, default `0.55` so clock mode
  and non-reveal countdown millis are pixel-unchanged.
- `drawTimerPreview()`: no new branch needed — confirm (don't just assume)
  that its existing millis path already reads `t.millisFullSize` through
  to `clockCompositeWidth`/`drawClockComposite`'s new `msScale` param.
- The seconds-field hint, including the degenerate "whole timer" wording
  from Behaviour.
- `timerEstimateText()`: no change needed — it already keys off
  `t.showMillis` alone for the 30fps estimate, which is still correct
  (the freeze doesn't change the fps, only how much of it is cached
  renderer-side, which the estimate doesn't model in detail today either).

**Validation + smoke agent** — `validation.py`, `scripts/smoke.py`,
`scripts/golden.py` (add job entries only — do not run `--record`; that
happens once, by the orchestrator, after the rendered output is signed
off, per CLAUDE.md).
- `validation.py`, inside `_validate_countdown_options`: the 3 new field
  checks (after the existing `fixed_format` check), exact messages as in
  the API contract. Add the 3 keys to the returned `clean` dict. No
  cross-field checks.
- `scripts/smoke.py` — add:
  - Pure: `_millis_ticking` — before threshold (`False`), at the boundary
    inclusive (`True`), after (`True`), reveal off (always `True`
    regardless of `rem_ms`), and the degenerate `reveal_seconds >= total`
    case (`True` at frame 0).
  - Pure: `_millis_size(size, False) == round(size*CLOCK_MS_SCALE)` and
    `_millis_size(size, True) == size` — and that
    `_digits_metrics(_millis_size(size, True))` matches
    `_digits_metrics(size)` on both `glyph_h` and `slot`, proving
    full-size millis actually match the main glyph size, not just
    approximately.
  - Validation: defaults (`millis_full_size`/`millis_reveal` both
    `False`, `millis_reveal_seconds == 60`) off a bare countdown dict,
    mirroring `check_fixed_format`'s existing pattern; the range error;
    both bool-type errors; both true together is accepted (no error);
    confirm a **clock-mode** payload silently ignores all 3 keys (same
    pattern as the existing `fixed_format` clock-mode-ignores check).
  - Real render + pixel check (**the seamlessness proof, must be
    pixel-checked, not assumed**): a 10s classic countdown, `hold_seconds:
    1`, `warn_last10: False`, `show_millis: True, millis_reveal: True,
    millis_reveal_seconds: 4`. At 30fps this crosses the threshold between
    output frames 179 (`rem_ms=4033`, frozen) and 180 (`rem_ms=4000`,
    live) — extract both with the bundled ffmpeg (`-vf "select='eq(n\,
    179)'" -vsync 0 -frames:v 1`, and again for 180). On each frame
    independently, find the bounding box of pixels with `sum(rgb) > 150`
    (comfortably between the vignette's ~25-49 and the digit colour's
    ~717-491) — assert the two boxes are the **same rectangle** (this is
    now a whole-composite check, not just the main digits, since nothing
    about the layout is state-dependent any more). Separately, on frame
    179 (the frozen one), assert the rightmost ~20% of that box (where the
    millis run sits) contains at least one pixel over the threshold —
    proving the millis glyphs are actually drawn (reading zero), not
    blank — rather than trusting that a present-but-invisible run would
    still pass a bounding-box check. (Smoke stays OCR-free per
    `scripts/smoke.py`'s own convention — this proves the glyphs are
    *present*, not that they read `.000`; that they read `.000` is
    already covered by the pure `_millis_ticking`/text-construction
    checks above.)
  - `golden.py`: add `timer/classic-millis-full-size` (6s classic,
    `show_millis: True, millis_full_size: True`, no reveal) and
    `timer/classic-millis-reveal` (6s classic, `show_millis: True,
    millis_reveal: True, millis_reveal_seconds: 4`) to `_build_jobs()`.
    Also add `timer/ring-millis-reveal-fixed` (6s ring, `show_millis:
    True, millis_full_size: True, millis_reveal: True,
    millis_reveal_seconds: 4, fixed_format: True`) — the single tightest
    combination found by measurement (ring, `fixed_format`, 100%), so a
    future change that regresses the sub-pixel margin noted above shows
    up as a hash mismatch rather than going unnoticed. Do not touch any
    existing job or run `--record`.

## Do not

- Do not change output when both new keys are at their defaults — must
  stay byte-identical (existing `timer/classic-millis` golden job is the
  proof).
- Do not modify `_render_digits`, `_render_clock_block`, `_paste_digits`,
  `base_for`, or anything in the clock-mode branch (`_render_clock` and
  everything it alone calls) — every new function in this spec is
  countdown-only and additive.
- Do not reintroduce an anchor rule, a reserved gap, or any positioning
  logic keyed on `millis_reveal` — the entire point of this design is that
  layout never depends on it. If a future change seems to need one, that
  is a sign the layout assumption above has been broken elsewhere first.
- Do not add validation refusals for any style/option combination — the
  measurements above found none needed; a future regression here should
  be caught by the new `timer/ring-millis-reveal-fixed` golden job, not by
  a hand-added rule.
- Do not merge `frozen_base_for` into `base_for` — keep them separate so
  the plain non-millis path's byte-identical guarantee is never at risk
  from a change made for this feature.
- Do not add a filename descriptor or an analytics prop for either option.
- Do not touch files outside your ownership list. If you believe you must,
  stop and say so in your report instead.
- Do not bump `version.py` or tag — the orchestrator does that.
- Do not run `golden.py --record` — add job entries only; recording
  happens once, after sign-off.

## Done means

- `SERVICE_VISUALS_STATS=0 .venv/bin/python scripts/smoke.py` passes,
  including every new check above.
- `SERVICE_VISUALS_STATS=0 .venv/bin/python scripts/golden.py --check`
  passes for every **existing** job unchanged (proves the off-by-default
  byte-identical claim); the 3 new jobs are then recorded once, by the
  orchestrator, after the renders below are eyeballed and signed off.
- CLAUDE.md's byte-identical guard, run because `render/timer.py`'s
  countdown branch changed: `git show HEAD:render/timer.py` (plus
  `encoder.py`, `fonts.py`) into a temp package, render 6s classic/ring/bar
  **plain** (no millis at all) old vs. new, extract frames, `cmp` them —
  must be identical.
- Orchestrator renders and *looks* at (per CLAUDE.md's "verify by
  rendering and looking" rule):
  - A real freeze countdown (classic) spanning the threshold — confirm by
    eye the digits genuinely don't move, and that `.000` reads as
    plausibly frozen rather than flickering.
  - A ring countdown with `fixed_format` and `millis_full_size` both on —
    the tightest measured case — confirm nothing visibly touches the ring.
  - The preview in the browser with Reveal toggled on/off and the seconds
    field changed live, at PORT=8799 — confirm frame 0 shows the frozen
    state correctly and the hint text is accurate.
- Update the knowledge graph as the last step, per the working agreement
  (`graphify . --update` on a subagent, Sonnet or Haiku only).
