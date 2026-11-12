# Spec: the QR card as one unit — text inside, drag, resize, dim

Supersedes the look in `qr-styles.md` (v1.30.0). The owner (2026-11-12),
with a screenshot of a heading floating above the card on a photo:

- "Card is simple, but when I click light, this is more of a background
  because it makes the whole screen white. And dots is a QR code style …
  so just fix that." "Add a couple or just refine the style."
- "I want the text to be inside the white border on both the scan here at
  the top, the title and the caption as well. It should be like one
  contained unit." "If there's no text, the white border can shrink to
  just show the QR code as well."
- "The position on screen just make it draggable … wherever the user
  drags the QR code, it is on screen. And also add a box around it so
  that the user can change the size."
- "Add a slider to determine how dark the background image is, because it
  could be that I made a screen with the hole for a QR code that it fits."

## Behaviour

| Thing | Behaviour |
|---|---|
| The card | One white rounded card holding heading (top), code, caption (bottom). No text outside it. No heading and no caption → the card is exactly today's square around the code. |
| Code style | `SQUARES` (default) · `DOTS` · `ROUNDED`. How the code's modules are drawn; nothing else changes. Finder and alignment patterns stay square in every style. |
| Background | `DARK` (default) · `LIGHT` · `IMAGE`. LIGHT is the off-white plate that used to be the "light style"; the card sits on it like on the others. IMAGE shows the upload and a **Dim** slider. |
| Dim | 0–80 %, default **0** (was a fixed 48 %): the text is inside the card now, so the photo needs no darkening to stay legible, and the owner's artwork may have a hole made for the code. Same name, range and default as the timer's. |
| Move | Drag the card on the preview. It always stays wholly on screen. The 3×3 grid stays as nine quick snaps. |
| Resize | A box with four corner handles around the card on the preview, and a SIZE slider (same value). The card never shrinks below scannable (9 px modules) or grows past the screen; the control snaps back to the size that was used. RESET = centre, 100 %. |
| Accent | Colours the ring only (gold text on white is unreadable). Text in the card is dark. |

## Display rules (renderer only — the preview is the renderer's own PNG)

All px at 1920×1080. `base_px = _module_px(n)` (today's rule, 16 px for a
short link). `module_px = clamp(round(base_px × size / 100), 9, fit)`,
where `fit` is the largest module size whose card is ≤ 1920 − 2·EDGE wide
and ≤ 1080 − 2·EDGE tall, `EDGE = 40`. `f = module_px / base_px`; the
**effective size** is `round(100 × f)`.

- Card: pad `46·f`, radius `40·f`. Inner block = code + 4-module quiet
  zone (unchanged). Width = inner + 2·pad, or wider if the text needs it.
- Heading: upper-cased, `label` font `76·f`, tracking `6·f`, colour
  `QR_DARK`. Caption: `caption` font `42·f`, `QR_DARK` at 78 % alpha.
  A line wider than the inner block shrinks to fit, to 70 % of its size
  at most; if still too wide the card widens to hold it.
- Vertical stack: pad · heading · `10·f` · inner block · `6·f` · caption ·
  pad. A missing line takes its gap with it. (The quiet zone already puts
  4 modules of white between text and code.)
- On the LIGHT background the card gets a 2 px `#d9d5cc` outline so its
  edge shows with the ring off.
- Position: card centre = `(x·1920, y·1080)`, then each axis clamped so
  the card stays `EDGE` inside the frame.
- Ring: as today, around the card's rectangle (no longer always square).
- Background: image (cover-fit, black scrim at `bg_dim` %) if `background`
  is set, else the DARK or LIGHT vignette plate.

## API (`/api/render` type `qr`, `/api/qr-preview`, `/api/qr-image`)

| Key | Values | Default | Error |
|---|---|---|---|
| `style` | `"squares"` `"dots"` `"rounded"` | `"squares"` | `That QR style is not valid.` |
| `backdrop` | `"dark"` `"light"` | `"dark"` | `That QR background is not valid.` |
| `bg_dim` | whole number 0–80 | 0 | `Dim must be a whole number between 0 and 80.` |
| `x`, `y` | number 0–1 | 0.5 | `Position must be a number from 0 to 1.` |
| `size` | whole number 50–200 | 100 | `Size must be a whole number between 50 and 200.` |

`position` is gone (the grid sends `x`/`y`). `url`, `heading`, `caption`,
`accent`, `ring`, `background`, `duration_seconds` unchanged.
`/api/qr-preview` adds two response headers: `X-QR-Box:
left,top,width,height` (the card, 1920×1080 px, integers) and
`X-QR-Size: <effective size>`. Analytics: the `style` prop keeps its name,
new values; nothing else is sent.

## UI (frontend agent)

Order of groups: Link or text · Text · Code style · Background · Position
and size · Ring colour · Length.

- **Code style**: segmented `qr-style` — SQUARES | DOTS | ROUNDED. The ring
  checkbox and its hint stay here.
- **Background**: segmented `qr-backdrop` — DARK | LIGHT | IMAGE. IMAGE
  reveals the upload row and `qr-bg-dim` (range 0–80, value shown as %),
  hint "Darkens your image. Leave at 0 if it's finished artwork." The
  uploaded file is kept when switching away and back; the payload sends
  `background` only while IMAGE is selected, and `backdrop: "dark"` then.
  IMAGE with nothing uploaded yet previews as DARK.
- **Position and size**: hint "Drag the code on the preview to move it;
  drag a corner to resize." The 3×3 grid (snaps: x and y each 0, 0.5, 1;
  the active cell clears as soon as the card is dragged), SIZE slider
  50–200 with the % shown, RESET link.
- **Preview**: an overlay box on the card (from `X-QR-Box`, scaled to the
  image) with four corner handles; cursor `grab` on the box,
  `nwse`/`nesw-resize` on handles. Dragging moves the box with the pointer
  and re-fetches the preview at most every ~120 ms, once more on release;
  when not dragging the box takes the server's `X-QR-Box`, and the SIZE
  slider takes `X-QR-Size` (so a clamp is seen, not silent), with a line
  under it saying why when it moved: "That's the biggest that fits on
  screen." / "That's the smallest a phone can still scan." Resize pins the
  opposite corner. A card sitting at a snap (dragged or clamped there)
  lights its grid cell. Dim is disabled until an image is chosen. The
  preview is sticky beside the form in the two-column layout. Pointer
  capture; works at 1024/900/768.
- "Accent colour" group is renamed **Ring colour** and hidden while the
  ring is unticked.

## Files (disjoint)

- Orchestrator: `render/qr.py`, `validation.py`, `app.py`,
  `scripts/smoke.py`, `scripts/golden.py` baseline, docs.
- Frontend agent (Sonnet): `static/index.html`, `static/js/qr.js`,
  `static/style.css`.

## Do not

Draw a light-on-dark code; let modules go below 9 px or off integer
boundaries; put text outside the card; send any text or file name to
analytics; touch other tiles.

## Done means

Smoke covers: the new keys' validation; no-text card is square; text
makes the card taller and sits inside it; drag clamps on screen; size
clamps to scannable and to the frame and is reported; dim 0 leaves the
image untouched; dots/rounded differ from squares and keep finders
square. Golden's two qr jobs re-recorded (the look changed on purpose).
Stills of every style × background looked at, and the preview driven in
the browser: drag, resize, snap, reset.
