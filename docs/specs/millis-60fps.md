# Spec: 60fps export for countdown milliseconds

**Status:** proposed, not yet built. Depends on docs/specs/millis-reveal.md
landing first (or in the same batch) — this spec reuses its names
(`millis_full_size`, `millis_reveal`, `millis_reveal_seconds`,
`_millis_ticking`, `_millis_size`, `frozen_base_for`) without re-deriving
them. The orchestrator will reconcile this spec against the real, merged
`render/timer.py` before implementation begins. **Owner:** orchestrator.
**Implementers:** three Sonnet agents (renderer, frontend + preview,
validation + smoke), one reviewer. No implementer may run on Opus or
Fable (CLAUDE.md).

## Why

The owner wants countdown milliseconds to export at 60fps so the ticking
`.mmm` looks smoother on the 60Hz screens most churches use. His original
idea — 30fps until the milliseconds start, then 60fps — is rejected, for
two independent reasons the orchestrator already established and this
spec records rather than re-argues:

1. A variable-frame-rate file causes stutter, audio drift and timeline
   problems in CapCut and ProPresenter, the owner's actual chain.
2. `render/encoder.py`'s `FrameEncoder`/`encode_parallel` take ONE
   `input_fps` for the whole ffmpeg pipe (`-r` appears exactly once before
   `-i` and once after the codec args, each fixed for the process's
   lifetime) — a mid-file switch is not a config flag away, it would need
   two separate ffmpeg processes concatenated, which is a different and
   much bigger feature this spec does not propose.

The alternative — a CONSTANT 60fps file for the whole clip, where the
frozen `.000` stretch is claimed to cost little because it is cached and
H.264 compresses repeats hard — was **unverified**. This spec measures
that claim before recommending anything.

## Blast radius (confirmed, not assumed)

Both direct source reading and a `graphify query` over this repo's graph
agree on the same fact, so this is stated with confidence: `encode_parallel`/
`FrameEncoder` have exactly six call sites total —

- `render_timer()` and `_render_clock()` in `render/timer.py` — the only
  two call sites that choose an fps at request time,
- `_render_aurora`/`_render_bokeh`/`_render_waves` in `render/motionbg.py`,
  each calling `encode_parallel(out_path, OUTPUT_FPS, ...)` — a hardcoded
  module constant, no options-driven branch,
- `render_spinner()` in `render/spinner.py`, calling `FrameEncoder(out_path,
  OUTPUT_FPS)` directly — same hardcoded constant,
- `render_qr()` in `render/qr.py`, calling `FrameEncoder(out_path,
  INPUT_FPS, output_fps=INPUT_FPS)` — its own local 15fps constant.

`render/scoreboard.py` never touches the encoder at all (PNG export only).
So a new fps option scoped to `render_timer`'s **countdown** branch cannot
leak into the spinner, QR, motion background or scoreboard renderers —
none of them read `options` for fps at all. This is a structural
guarantee, not a promise to be careful.

## Measurements

All rendering below used the project's real `render/encoder.py`
(`encode_parallel`/`FrameEncoder`, the same bundled ffmpeg, the same
libx264 flags — `SERVICE_VISUALS_ENCODER=libx264`, matching what
`golden.py` forces for determinism and what this Mac always picks anyway).
Digit drawing was a small **local** re-implementation of
`_digits_metrics`/`_render_clock_block`'s shapes — not an import of
`render/timer.py`, which is being edited concurrently by other agents
right now and must not be relied on mid-edit. Content: classic-style
digits at 362px (the real measured 100%-fit size for `"15:00.000"` from
docs/specs/millis-reveal.md's own table) over the standard vignette
background — i.e. representative of the **cheapest** style. Ring/bar add
a per-frame mask/overlay cost this benchmark does not include (see
caveats below). Machine: Apple M3 (arm64), Pillow 11.3.0,
`SERVICE_VISUALS_STATS=0`, all output to a scratch temp dir —
`exports/` was never touched and is confirmed still empty.

**1 & 2 — file size and render time, real encode path:**

| Scenario | fps | frames | render time | file size | time/frame | bytes/frame |
|---|---:|---:|---:|---:|---:|---:|
| A. Static (one unchanging frame, 20s) | 30 | 600 | 1.76s | 141,621 B | 2.93ms | 236.0 B |
| A. Static | 60 | 1,200 | 3.44s | 219,882 B | 2.87ms | 183.2 B |
| B. Frozen (per-second cache, ms pinned `.000`, 24 distinct seconds) | 30 | 720 | 2.22s | 197,418 B | 3.08ms | 274.2 B |
| B. Frozen | 60 | 1,440 | 4.41s | 302,066 B | 3.06ms | 209.8 B |
| C. Ticking (every frame unique, 8s) | 30 | 240 | 0.96s | 323,267 B | 4.00ms | 1,346.9 B |
| C. Ticking | 60 | 480 | 2.03s | 474,285 B | 4.23ms | 988.1 B |

Ratios (60fps ÷ 30fps): static size **1.55x**, time **1.95x** · frozen
size **1.53x**, time **1.99x** · ticking size **1.47x**, time **2.11x**.

Scenario B mirrors `frozen_base_for` exactly: a cache hit returns the
*same* Python object with no `.copy()`, matching classic style's real
`return base` path — so this is the best case the real frozen stretch can
ever do, not a pessimistic guess.

**The "near-zero" premise is half right, and the spec says so plainly, per
the instruction to say so if the measurement disagrees.** Frozen content
*is* dramatically cheaper than ticking content **at the same fps** — about
4-5x less per second of output, in both time and bytes (compare frozen's
8.0-12.3 KB/s of output against ticking's 39.5-57.9 KB/s). That part of
the premise holds. But **doubling fps is not free even for frozen
content** — it costs essentially the *same* ~1.5x size / ~2x time
multiplier as it does for ticking content (1.53x/1.99x vs 1.47x/2.11x —
statistically the same). The reason is structural, not a fluke: every
emitted frame — cached PIL object or not — still needs its own
`image.tobytes()` call, its own write through the ffmpeg pipe, and its own
(cheap but non-zero) H.264 slice. Caching removes the **Python-side
drawing** cost (~1.0-1.1ms/frame — the gap between the ~3.0ms cached rate
and the ~4.1ms uncached rate) but not the **per-frame pipe-and-encode
floor** (~3.0ms/frame), which dominates total wall-clock time and scales
with total frame count regardless of caching. So: caching makes the
frozen stretch's *base* cost small; it does not make *doubling* that cost
free. The recommendation below survives this correction only because
"small, doubled" is still small in absolute terms — not because the
doubling itself is free.

**3 — genuine constant 60fps, probed, not assumed:**

```
Stream #0:0: Video: h264 (High) ..., yuv420p, 1920x1080, 60 fps, 60 tbr, 15360 tbn
decoded frames: 480 (== 8s x 60fps exactly)
distinct inter-frame deltas: [0.016666, 0.016667]   (== 1/60, float rounding only)
mean delta 0.016667s, stdev 0.000000s
```
(via the bundled ffmpeg's `showinfo` filter on the decoded stream, not the
container header alone.) The 30fps baseline probed the same way returned
deltas of `[0.033333, 0.033334]`, stdev `0.000000s`. **Confirmed: a
genuine constant-frame-rate stream, evenly spaced, not a relabeled or
variable-rate one.**

**4 — how input_fps/output_fps behave today, and what that means for "60fps":**

Read directly from `render/timer.py` and confirmed by graphify: a plain
(non-millis) countdown renders at a **low** `input_fps` (`_input_fps()`:
1fps classic, 2-10fps ring/bar depending on total) and ffmpeg's own `-r`
flags **duplicate** those frames up to `TIMER_OUTPUT_FPS` (15). A millis
countdown forces `fps = out_fps = 30` — input and output already equal,
so every encoded frame is already unique; nothing is duplicated today.

This distinction is the whole ballgame for what "60fps" is allowed to
mean here. Proven empirically (not just read from a docstring): a tiny
clip encoded with `input_fps=3, output_fps=30` decodes to exactly 30
frames whose content hashes fall into **three runs of exactly 10
identical frames each** — i.e. ffmpeg repeats each source frame
`output_fps / input_fps` times; it does not synthesize new in-between
content. **If this feature only bumped `output_fps` to 60 while leaving
`input_fps` at 30, the result would be a bigger file that decodes to
double-duplicated copies of the exact same 30 unique values per second —
zero smoothness gain, pure waste.** Genuine smoothness requires
`input_fps = output_fps = 60`: 60 truly distinct `ms` values a second
(`round(i * 1000 / 60)` steps of ~16.7ms) instead of 30 (~33.3ms steps).
**This spec mandates `input_fps == output_fps == 60` for exactly that
reason — see Frame rate rules below.**

**Cross-check against a documented reference:** `validation.py`'s own
comment states a 2-hour (7200s) fully-uncached 30fps millis countdown is
"~20 minutes to render" — 216,000 frames / 1200s = 5.56ms/frame, about
35% slower than this benchmark's measured 4.0-4.2ms/frame. That gap is
plausible (this benchmark's content is leaner than the real renderer's:
no warn-colour branch, no background-plate cycling, no alpha check) and
is treated as a signal that **this benchmark's absolute times are a lower
bound**, not an upper one. The *ratios* (1.5x size, ~2x time) do not
depend on which absolute rate is right, and are the numbers the decisions
below actually rest on.

**Caveats, stated plainly:**
- Ring/bar styles add a real per-frame cost this benchmark does not
  model: `_ring_mask(frac)` redraws a supersampled arc mask **every
  frame**, cached or not (it depends on continuously-advancing `frac`,
  not on the cached text). At 60fps that mask math runs twice as often.
  Not measured directly; flagged for the reviewer to eyeball a real ring
  render (CLAUDE.md's "verify by rendering and looking").
- Measured on one fast Apple Silicon Mac. A volunteer's older or Windows
  machine (no hardware H.264 encoder tested here — CLAUDE.md: "Windows
  can't be tested here") could be meaningfully slower. Treat every
  absolute number above as optimistic.

## Decisions (each tied to a number above)

- **Opt-in, default off, byte-identical when off.** Confirmed, not just
  taken on faith: existing millis output is pixel-hash-guarded by
  `golden.py` (`timer/classic-millis`, `clock/classic-12h-millis`, plus
  millis-reveal.md's three pending jobs) — a silent default-on change
  would move every one of those hashes since frame *count* alone changes
  the decoded byte stream. Combined with a real, measured ~2x render-time
  and ~1.5x file-size cost, this is exactly the kind of change a volunteer
  must choose, not receive silently.
- **A new, lower duration ceiling at 60fps: `MILLIS_MAX_SECONDS_60FPS =
  900`** (15 minutes), enforced in addition to the existing
  `MILLIS_MAX_SECONDS = 1800`. The number is not arbitrary: 900s x 60fps =
  54,000 frames, and 1800s x 30fps (today's existing worst case) = 54,000
  frames — **exactly equal**. Turning 60fps on can therefore never make
  the worst case slower or bigger than what already ships today — it can
  only make it different. Checked under both rate assumptions above,
  including the default 5s hold in the frame count (so 1,805s@30fps =
  54,150 frames vs 905s@60fps = 54,300 frames — as close to equal as the
  discrete second-based hold allows): today's 1800s/30fps worst case is
  ~217-301s / ~72.9MB; the capped 900s/60fps worst case is ~230-302s /
  ~53.7MB (smaller in bytes despite the near-identical frame count,
  because 60fps content compresses slightly better per frame —
  closer-together frames are more similar). Without this cap, an uncapped
  1800s/60fps render would run ~458-602s (7.6-10.0 min) at ~107MB — the
  "unacceptably slow" case the task asked me to flag if the numbers showed
  it. They do, so the cap is load-bearing, not decorative.
- **Both plain `show_millis` (ticking the whole way) and `millis_reveal`
  may use 60fps**, gated by the single duration cap above rather than by
  banning one combination outright — consistent with how this codebase
  already handles the analogous risk (`MILLIS_MAX_SECONDS` caps rather
  than forbids). The cap is what makes the plain-ticking case tolerable;
  there is no separate reason to forbid it once the cap exists. The
  flagship case — 15 minutes with `millis_reveal` at 60s — is cheap in
  absolute terms either way: ~85s/9.5MB today at 30fps vs ~171s/14.4MB at
  60fps, a ~1.4-minute, ~4.9MB cost for the smoother finish.
- **Countdown only, not clock mode.** Matching millis-reveal.md's own
  precedent and reasoning exactly (a clock ticks forward forever — "near
  the end" has no meaning for it), and reinforced by this spec's own
  numbers: clock-mode millis is *already* the worst-case shape (every
  frame live/uncached, no freeze to cache) for the full length of
  `duration_seconds` — it is the least cost-effective place to spend this
  feature's budget, and folding it in now would add scope to a spec the
  sister feature has already scoped away. Deferred, not rejected.
- **`millis_60fps` composes with `millis_full_size`/`millis_reveal` with
  zero new interaction code.** Every function `_millis_ticking`,
  `_millis_size`, `_render_clock_block`, `_paste_digits`, `base_for`,
  `frozen_base_for` expresses its threshold in **milliseconds**
  (`rem_ms`), which is already fps-independent — only the frame-index to
  `rem_ms` mapping (`round(i * 1000 / fps)`, already parameterised on
  `fps`) changes. The frozen cache's key is the displayed **text**, not
  the frame index, so the cache does not grow or need retuning at 60fps —
  it still holds at most `bases_cap` distinct seconds, just serves each
  one to twice as many frames.

## Behaviour (what the operator sees)

One new control, in the existing **Milliseconds** group inside the
`Advanced` details (alongside Full-size milliseconds / Hold at zero until
the end / Seconds before the end from millis-reveal.md) — countdown mode
only; hidden in clock mode by `applyTimerMode()`, the same mechanism that
hides BAR there today.

| Control | Details |
|---|---|
| Smoother milliseconds (60 fps) | Checkbox, `timer-millis-60fps`, default off. Only visible/meaningful in countdown mode. Disabled (with a tooltip) whenever the countdown's current total exceeds 15 minutes; if the operator lengthens an already-checked timer past that, the checkbox auto-unchecks the same way switching to clock mode auto-falls-back BAR to CLASSIC. |

Hint text (always visible under the checkbox, matching the plain,
concrete, numbers-first register `docs/specs/alpha-export.md`'s format
choice already established in this UI — see exact string below).

## Frame rate rules (the heart of this spec)

- `millis_60fps` only ever changes ONE thing: which `(fps, out_fps)` pair
  `render_timer`'s countdown branch feeds into `encode_parallel`. It never
  touches layout, sizing, colour, or the freeze/tick threshold formula.
- Selection, replacing today's `if show_millis: fps, out_fps = 30, 30`:
  ```python
  if show_millis:
      if millis_60fps:
          fps, out_fps = 60, 60
      else:
          fps, out_fps = 30, 30
  else:
      fps = _input_fps(style, total)
      out_fps = TIMER_OUTPUT_FPS
  ```
- **`input_fps` and `output_fps` must be set equal (60/60), never
  30-in/60-out.** Proven above: ffmpeg only *duplicates* frames to reach a
  higher output fps; it invents no new content. 30-in/60-out would be a
  strictly-worse file (bigger, same visual information) — not a cheaper
  version of the real feature. This is a hard rule, not a style
  preference.
- `total_frames = (total + max(1, hold)) * fps` — unchanged formula, `fps`
  now possibly 60. `make_frame`'s existing `rem_ms = max(0, total*1000 -
  int(round(i * 1000.0 / fps)))` already generalises correctly for
  `fps=60` with no edit — it was never hardcoded to 30, only ever fed 30.
- The frozen-cache key (`(text, color, idx)` in `frozen_base_for`) and the
  live/ticking branch are otherwise byte-for-byte what millis-reveal.md
  already specifies. Nothing about `_millis_ticking`,
  `millis_reveal_seconds`'s 1-1800 range, or the warn-colour
  `rem_ms <= 10_000` check changes.
- `MILLIS_MAX_SECONDS_60FPS = 900` applies **in addition to**
  `MILLIS_MAX_SECONDS = 1800`, checked first (it is the tighter bound)
  whenever `millis_60fps` is on:
  ```python
  if show_millis and millis_60fps and total > MILLIS_MAX_SECONDS_60FPS:
      raise ValidationError(
          "With smoother milliseconds (60 fps) on, the timer can run "
          "for at most 15 minutes. Turn 60 fps off for a longer timer.")
  if show_millis and total > MILLIS_MAX_SECONDS:
      raise ValidationError(...)   # existing check, unchanged
  ```
- `millis_60fps` is accepted and validated **regardless of `show_millis`**
  (like `fixed_format`/`millis_full_size`/`millis_reveal` before it) — it
  is simply inert when millis are off. `render_timer({"show_millis":
  False, "millis_60fps": True, ...})` must take the exact same
  `_input_fps()`/`TIMER_OUTPUT_FPS` path as `millis_60fps` being absent —
  a pure no-op, proven by smoke (see below), not merely asserted.
- Clock mode (`_validate_clock_options`, `_render_clock`) never reads
  `millis_60fps` — silently ignored if a caller sends it, same pattern as
  every countdown-only key today.

## API contract (type stays `"timer"`, countdown payloads only)

```json
{"type": "timer", "options": {
  "minutes": 15, "seconds": 0, "style": "classic",
  "accent": "#e8b44f", "warn_last10": true, "hold_seconds": 5,
  "show_millis": true,
  "millis_reveal": true,
  "millis_reveal_seconds": 60,
  "millis_60fps": true
}}
```

- `millis_60fps` — bool, default `false`. Not a bool →
  *"Smoother milliseconds (60 fps)" must be true or false.*
- Duration ceiling, whenever `millis_60fps` is true: 1 to
  `MILLIS_MAX_SECONDS_60FPS` (900) seconds total — error text above. This
  is a **tighter** ceiling layered on top of the existing
  `MILLIS_MAX_SECONDS` (1800) check, not a replacement for it: with
  `millis_60fps` off, behaviour is exactly today's 1800s ceiling.
- No cross-field checks against `millis_full_size`, `millis_reveal`, or
  `style` — the Decisions section above shows there is nothing left to
  refuse; every combination is valid, subject only to the one duration
  ceiling.
- **Byte-identical when off:** `millis_60fps` defaulting `false` (or
  `show_millis` itself `false`) takes the exact code path that exists
  today — `golden.py`'s existing jobs, and millis-reveal.md's pending
  ones, must match their recorded hashes unchanged.
- **Filename:** append `_60fps` immediately after the existing `_ms`
  descriptor, only when `show_millis and millis_60fps`:
  `timer_15m00s_classic_ms_60fps_<stamp>.mp4`. This mirrors `_ms` itself
  (a frame-rate/timing property gets a filename marker) rather than
  `fixed_format`/`millis_full_size`/`millis_reveal` (cosmetic refinements,
  no marker) — 60fps is a real, technically-meaningful property of the
  file the operator might need to tell apart from a 30fps export of the
  same timer.
- **Analytics:** no new prop. `_timer_props` sends only `mode`, `style`,
  `bg` today; `show_millis`, `fixed_format`, `millis_full_size` and
  `millis_reveal` are already not surfaced. `app.py` needs no change.

## UI copy (exact strings)

- Checkbox label: **Smoother milliseconds (60 fps)**
- Hint (always visible, id `timer-millis-60fps-hint`):
  > Turn this on when the ticking numbers are the whole point — a
  > dramatic final countdown viewed up close. It roughly doubles render
  > time and makes the file about 50% bigger. Only helps if your editor
  > also exports at 60 fps: export at 30 and the extra frames are thrown
  > away.

  (Orchestrator revision. The editor sentence was added because the
  owner's real chain is Service Visuals → CapCut → MP4 → ProPresenter, so
  a 30 fps CapCut export silently discards the whole feature — the one
  failure an operator would never see. "Most people won't notice from
  normal seating" was removed: it is an unmeasured perceptual claim, while
  the time and size figures that remain are measured.)
- Disabled-state tooltip (checkbox `title`, shown whenever total duration
  exceeds 15 minutes; matches the `timer-bg-add`-at-cap precedent of a
  plain `.disabled`/`.title` pair rather than a paragraph):
  > Needs 15 minutes or less. Shorten the timer to use smoother
  > milliseconds.
- Validation error (server-side backstop; same wording the tooltip
  implies, in the established `_int_field`-adjacent register):
  > With smoother milliseconds (60 fps) on, the timer can run for at most
  > 15 minutes. Turn 60 fps off for a longer timer.

No change to `timerEstimateText()`'s general shape — it already keys fps
off `t.showMillis`; it now also reads `t.millis60fps` and multiplies by 2
(`var fps2 = t.showMillis ? (t.millis60fps ? 60 : 30) : (...)`), so the
live "EST. RENDER ~Xm Ys" line already reflects the real cost before
export, which is the concrete, per-render number the hint text above
deliberately does not try to restate in the abstract.

## New golden jobs (entries only — nobody runs `--record`)

`scripts/golden.py --record` **overwrites `golden.json` wholesale with
only the jobs that ran** (`run_jobs()`'s result dict is written verbatim;
a `--record --only X` run would silently delete every other job's
baseline). Add these three entries to `_build_jobs()`; do not run
`--record`, with or without `--only`, at any point in implementing this
spec. The orchestrator records once, after rendering and looking, exactly
as millis-reveal.md's own three pending jobs are recorded once.

```python
("timer/classic-millis-60fps", lambda: _timer(
    {"minutes": 0, "seconds": 6, "style": "classic",
     "accent": "#e8b44f", "warn_last10": True,
     "hold_seconds": 2, "show_millis": True,
     "millis_60fps": True})),
("timer/classic-millis-60fps-reveal", lambda: _timer(
    {"minutes": 0, "seconds": 6, "style": "classic",
     "accent": "#e8b44f", "warn_last10": True,
     "hold_seconds": 2, "show_millis": True,
     "millis_60fps": True, "millis_reveal": True,
     "millis_reveal_seconds": 4})),
("timer/ring-millis-60fps-reveal", lambda: _timer(
    {"minutes": 0, "seconds": 6, "style": "ring",
     "accent": "#e8b44f", "warn_last10": True,
     "hold_seconds": 2, "show_millis": True,
     "millis_60fps": True, "millis_reveal": True,
     "millis_reveal_seconds": 4})),
```

The first pins the plain 60fps path; the second and third pin the
frozen-cache-at-60fps interaction on classic and ring respectively — ring
because it is the one style with its own per-frame overlay cost this
spec's benchmark did not model (see caveats), so a future regression
there shows up as a hash mismatch instead of going unnoticed.

## Smoke plan (proving genuine 60fps, not just claiming it)

- **Pure:** a new tiny function `_millis_fps(millis_60fps)` returning `60`
  if true else `30` — the one place this decision is made, nothing computes
  `60 if ... else 30` inline elsewhere. Check both branches.
- **No-op proof:** render (or otherwise compare) `show_millis=False,
  millis_60fps=True` against `show_millis=False` alone — assert identical
  output, proving the "inert when millis are off" claim rather than
  assuming it.
- **Real render, probed with the bundled ffmpeg** (mirroring this spec's
  own measurement methodology, promoted to a permanent regression check):
  1. Render a short (6s) classic countdown with `show_millis: True,
     millis_60fps: True`. Run `showinfo` on the decoded stream; assert
     frame count equals `duration * 60` exactly and every inter-frame pts
     delta equals `1/60` within float tolerance — proving a genuine
     constant 60fps stream, not a relabeled 30fps one.
  2. **Uniqueness during the live stretch:** hash each decoded frame
     within one displayed second of the *ticking* branch; assert no two
     are identical — proving real per-frame content, not duplicated
     frames wearing a 60fps label (the exact failure mode measurement 4
     demonstrated is possible).
  3. **Identity during the frozen stretch:** render a `millis_reveal`
     countdown at 60fps: frame `k` and frame `k+59` (dropping the
     leading digit-of-a-new-second boundary, so both are within one
     frozen displayed second) should decode to byte-identical content —
     proving the cache is actually being exercised at 60fps, not
     silently rebuilding every frame.
  4. **Seamlessness across the threshold, recomputed for 60fps:** adapt
     millis-reveal.md's own frame-179/180 bounding-box proof to 60fps.
     Recomputed directly from `_millis_ticking`'s formula for the same
     10s/`hold=1`/`millis_reveal_seconds=4` shape the sister spec uses —
     NOT a naive 2x of 179/180 (that would give 358/360 and land on the
     wrong side of the boundary once rounding is accounted for): at 60fps
     the last frozen frame is **359** (`rem_ms=4017`) and the first live
     frame is **360** (`rem_ms=4000`). Verify these two numbers against
     the shipped formula at implementation time rather than trusting this
     arithmetic blind — extract both frames and assert the same
     bounding-box-match technique holds.
- **Validation:** `millis_60fps` bool-type error; the 900s ceiling error
  fires for `total=901..1800` when `millis_60fps` is true and does NOT
  fire for the same totals when it is false (proving the two ceilings are
  independent, not one replacing the other); a clock-mode payload with
  `millis_60fps` set is silently ignored (mirrors the existing
  `fixed_format`-in-clock-mode check).

## Files & ownership (agents edit ONLY their own files)

**Renderer agent** — `render/timer.py`, `README.md`.
- New pure function `_millis_fps(millis_60fps)`.
- `render_timer`'s countdown branch: read `millis_60fps` (already
  validated); replace the `if show_millis: fps, out_fps = 30, 30` line
  per Frame rate rules above. Add `_60fps` to the filename descriptor
  (only when `show_millis and millis_60fps`) alongside the existing `_ms`
  suffix. No other line in `render_timer`, and nothing in `_render_clock`,
  changes.
- `README.md`: one clause on the existing Timer bullet.

**Frontend + preview agent** — `static/index.html`, `static/js/timer.js`.
- Markup: one checkbox, id `timer-millis-60fps`, inside the Milliseconds
  group, countdown-mode-only (hidden via `applyTimerMode()`).
- `readTimer()`/`timerPayload()`: add `millis60fps` to the countdown
  branch only.
- Disable + auto-uncheck logic keyed on total duration vs. a mirrored
  `900` constant (comment pointing at `MILLIS_MAX_SECONDS_60FPS` in
  validation.py, same convention as the existing `MILLIS_MAX_SECONDS`
  mirror).
- `timerEstimateText()`: fold `t.millis60fps` into the existing
  `fps2`/`fps` ternary as shown under UI copy above.
- No preview-canvas change: frame 0's appearance is identical regardless
  of fps (this spec changes timing, never layout), so
  `drawTimerPreview()`/`clockCompositeWidth()`/`drawClockComposite()` need
  no edits.
- The hint and tooltip strings verbatim from UI copy above.

**Validation + smoke agent** — `validation.py`, `scripts/smoke.py`,
`scripts/golden.py` (entries only — never `--record`).
- `validation.py`: `MILLIS_MAX_SECONDS_60FPS = 900` constant next to
  `MILLIS_MAX_SECONDS`. Inside `_validate_countdown_options`: the
  `millis_60fps` bool check, the layered ceiling check (tighter check
  first), add `millis_60fps` to the returned `clean` dict.
- `scripts/smoke.py` / `scripts/golden.py`: everything under Smoke plan
  and New golden jobs above.

## Do not

- Do not implement 30-in/60-out. Measurement 4 proves that duplicates,
  not smooths — it is not a cheaper version of this feature, it is a
  different and pointless one.
- Do not make 60fps the default, or change output when `millis_60fps` is
  absent/false — the existing golden jobs are the proof.
- Do not allow 60fps millis in clock mode in this pass — deferred, per
  Decisions.
- Do not touch `_millis_ticking`, `_millis_size`, `_render_clock_block`,
  `_paste_digits`, `base_for`, `frozen_base_for`'s cache-key shape, or any
  sizing/layout code — this feature is frame-rate only.
- Do not run `scripts/golden.py --record`, with or without `--only`, at
  any point — it overwrites the whole baseline. The orchestrator records
  once, after render-and-look sign-off.
- Do not render to `exports/` while testing — use a temp dir
  (`SERVICE_VISUALS_EXPORTS` override, as `golden.py` itself does) and
  leave `exports/` exactly as it was found.
- Do not touch files outside your ownership list. If you believe you
  must, stop and say so in your report instead.
- Do not bump `version.py` or tag — the orchestrator does that.

## Done means

- `SERVICE_VISUALS_STATS=0 .venv/bin/python scripts/smoke.py` passes,
  including every new check above.
- `SERVICE_VISUALS_STATS=0 .venv/bin/python scripts/golden.py --check`
  passes for every **existing** job unchanged (proves byte-identical when
  off); the 3 new jobs are recorded once, by the orchestrator, after the
  renders below are eyeballed and signed off.
- CLAUDE.md's byte-identical guard, run because `render/timer.py`'s
  countdown branch changed again: `git show HEAD:render/timer.py` (plus
  `encoder.py`, `fonts.py`) into a temp package, render 6s classic/ring/bar
  **plain** (no millis at all) old vs. new, extract frames, `cmp` them —
  must be identical.
- Orchestrator renders and *looks* at (CLAUDE.md's "verify by rendering
  and looking"):
  - A real 15-minute `millis_reveal`-at-60s countdown at 60fps, classic
    style — confirm the ticking stretch visibly reads smoother than the
    30fps version side by side, and that the frozen-to-live transition is
    still seamless (no jump) at the new fps.
  - A ring countdown with `millis_60fps` on — confirm by eye that the
    ring overlay (this spec's one unmeasured cost) still looks and
    performs acceptably.
  - The disabled-checkbox behaviour in the browser at PORT=8799: lengthen
    a 60fps-checked countdown past 15 minutes and confirm it auto-unchecks
    with the tooltip explaining why.
- Update the knowledge graph as the last step, per the working agreement:
  `graphify update .` on a subagent (Sonnet or Haiku only) — not a bare
  `graphify .`, which would ask for an LLM key to read `docs/`.
