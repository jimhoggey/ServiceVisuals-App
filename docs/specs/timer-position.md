# Spec: place the countdown digits (drag on the preview)

The owner (2026-10-15), with a Summit 2026 background whose speech bubble is
the obvious place for the timer: "I want it to be easy, that the user can
drag the timer UI into this box … a very, very simple task." Small by
design: position and size, nothing else.

## Behaviour

| When | Behaviour |
|---|---|
| Countdown, CLASSIC style | Drag the numbers on the preview to move them. A SIZE slider (20–100 %) shrinks them. RESET puts them back in the middle at full size (named RESET, not CENTRE, because it resets the size too). |
| Countdown, RING / BAR | The controls are replaced by one line: "Placing the numbers works with the Classic style — your placement comes back when you pick it again." The placement is kept. |
| RING / BAR, clock mode | Unchanged and not offered: ring and bar are drawn around fixed centres, and the clock has its own layout. |
| Never touched | `digits_x = 0.5`, `digits_y = 0.5`, `digits_size = 100` → byte-identical to before. |
| Edges | The digits are always kept wholly on screen: a centre too close to an edge is moved in just far enough (renderer and preview alike). |

## Number colour and dim (addendum, same day)

The owner: "when I click accent colour … it changes the colour of the
number. At the moment it's only white" and "don't automatically dim
anything".

- ACCENT COLOUR gains a **WHITE** swatch, first and the default. WHITE =
  today's look exactly: white numbers, amber (`#e8b44f`) ring/bar and
  last-10-seconds warning; the payload sends `accent: "#e8b44f"` and
  `digits_color: ""`.
- Any other swatch or CUSTOM: `accent` and `digits_color` are both that
  colour — numbers, ring and bar in it. The warning can't be the accent
  any more, so it is red `#e2725b`, or white if the numbers are that red.
- Countdown only; the clock and the beat opener's ink-on-white are
  unchanged. `digits_color`: "" or `#rrggbb`, else `Number colour must be a
  6-digit hex color like "#e8b44f".`
- Background **Dim** defaults to 0 (was 45) in the slider, validation and
  renderer: backgrounds are finished artwork, and 45 % greyed a white
  speech bubble out.

## Display rules (renderer and preview)

- Size: the classic auto-fit size (with or without milliseconds) × `digits_size / 100`, rounded, at least 20 px.
- Centre: `(digits_x × width, digits_y × height)`, rounded; then the block's
  left/top = `clamp(centre − size/2, 0, frame − size)` per axis.
- The beat opener (classic, white beats) honours the same placement.

## API (countdown only)

| Key | Type | Default | Error |
|---|---|---|---|
| `digits_x`, `digits_y` | number 0–1 | 0.5 | `Position must be a number from 0 to 1.` |
| `digits_size` | whole number 20–100 | 100 | `Size must be a whole number between 20 and 100.` |

Clock mode ignores them. Ring/bar accept and ignore them.

## UI

In the STYLE group, shown only for a CLASSIC countdown: hint "Drag the numbers on the preview to place them — handy for a gap in your background.", SIZE slider, CENTRE button. Cursor `grab` over the numbers.

## Do not

- Change any output when the three keys are at their defaults.
- Touch ring, bar or clock rendering.
- Send position or size to analytics.

## Done means

Smoke: validation, defaults byte-identical (golden + old-vs-new),
a placed render has its digits in the expected region, the clamp keeps them
on screen. Preview dragged in the browser; a real frame inspected.
