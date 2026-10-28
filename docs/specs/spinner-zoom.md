# Spec: CINEMATIC ZOOM for the spinner

The owner (2026-10-28), with a 300-name youth-conference wheel: "as the
spinner … cinematically slows down … the video could zoom in on the picker,
on the arrow, as it slowly passes, and then the names can become more
visible." A toggle. Answers: zoom over **the last half of the spin**; when
it lands, **stay zoomed, then the WINNER card fades in over it**.

Revised after the first cut: "Zoom closer. I want to see like 10 names
total on the big screen and still make it look like a circle … it should
like slow down and really build hype as it ticks over and slowly slows
down to the person that wins it." And: "you see the big arrow on screen
as to where it's going to land." (A sketch: the rim arcing across the top,
a big arrow above it, a handful of wide slices fanning down.)

Third cut: "Why does it first zoom towards the middle and then move up? …
it should just naturally zoom in on the top where the picker is … the
wheel … occupy like half the screen, and the top half is the picker, the
big triangle." And: "add this cinematic zoom as a time allocation thing
… I want to have a spin, but then I also want it to slowly zoom in, but
then have some suspense build while it's ticking over each of the names."

## Behaviour

Timeline with the zoom on: WAIT → wind-up (0.8 s) → **SPIN** → **ZOOM** →
WINNER. The wheel lands at the end of ZOOM.

| When | Behaviour |
|---|---|
| Toggle off (default) | Byte-identical to today (cubic spin, bitmap wheel, no ZOOM phase). |
| SPIN | The wheel as today (bitmap path), slowing evenly into the ZOOM phase's speed — no lurch where they meet. |
| ZOOM, first 45 % | One straight push-in on the arrow: the top of the rim glides from y = 110 to the middle of the screen while z goes 1 → Z_max; the arrow grows to fill the top half. |
| ZOOM, the rest | Held close-up: ~10 names across the bottom half, the rim curving across the middle, the big arrow above. About 5 names crawl past, slowing to a stop on the winner; the arrow flicks as each one goes under it. |
| Landed (+0.2 s, over 0.4 s) | Every slice but the winner's darkens to 62 % black; the WINNER card fades in at the top, above the arrow. |
| Names | Past 100 entries, names fade in on screen size 9 → 14 px, so the 1× wheel shows no static. |

## Motion

- ZOOM rotation eases out cubically over `D = 5·Z / (3·0.55²)` slices
  (`zoom_travel`), so it is crossing 5 names/s when the push-in ends. SPIN
  decelerates linearly from `v0 = 2·D_spin/S − v1` to `v1 = 3·D/Z`; whole
  turns are added if needed so v0 > v1 (`zoom_full_spins`).

## Geometry (1080p px; the JS preview scales to its canvas)

- `Z_max = clamp(1920·n / (10 · 2π · R), 1.6, 24)` — 10 slices across the
  width at the rim. 300 names ≈ 21×; 12 names lean in at 1.6×.
- Camera: `z = Z_max^u` (u the eased push-in). The world point at the top
  of the rim (960, 110) maps to (960, 110 + u·430); identity at u = 0.
  Everything else interpolates by u.
- Slices: polygons for the slices in shot, gaps a fixed 4 px on screen.
- Names: one shared size. `px = (base + u·(end/Z_max − base))·z`, where
  `base` is the wheel's own size (≤100) or 0.7 × the slice arc at the label
  radius (>100), and `end = max(min(80, 0.45 × slice width at the rim),
  base·Z_max)`, then shrunk (×0.96) so the longest name fits
  `run = min(440, (R − hub − 60)·Z_max)` — the bottom half. Centre radius
  lerps from 0.62 R to `R − (tip_in + 24 + run_i/2)/z`: they hang from the
  rim below the arrow's tip.
- Pointer: `s = 1 + 5.5·u`; tip `tip_in = 38 − 18·u` px into the rim, so
  `top = rim_y + tip_in − 56·s`. Flick: `−14° × pointer_flick(...)`,
  pivoting on the middle of its top edge.

## API

Spinner option `zoom`: bool, default false (`"Cinematic zoom" must be true
or false.`). `zoom_seconds`: whole seconds 2–30, default 6 (`Zoom length
must be a whole number between 2 and 30.`), used only with the zoom on.
Filename gains `_zoom`. Analytics: none new.

## UI

A **Cinematic zoom** checkbox in the spinner's Timing group, hint: "After
the spin, the camera pushes in on the arrow and the last names tick past
slowly. Set how long under ZOOM. Best with a lot of names." While it is
ticked, a ZOOM seconds field and a ZOOM segment in the timing bar appear
between SPIN and WINNER, and Total video includes it. TEST SPIN previews
the same move on the canvas.

## Do not

Change any output with `zoom` off (golden + old-vs-new); zoom the WINNER
card; let a 300-name wheel show label static at any zoom; let the arrow
leave the shot or cover the winner's name.

## Done means

Smoke: validation; zoom off unchanged; zoom on renders, filename marker,
~10 names in shot at 300, the window settles before landing, the close-up
differs from the 1× frame. Frames of 300- and 12-name zoom renders
inspected: names readable as it slows, the winner under the arrow, the
reveal dim and the card clear of the winner's name.
