# Spec: Transparent (alpha-channel) export for the Timer tile

**Status:** proposed, not yet built. **Owner:** orchestrator. **Implementers:**
four Sonnet agents — renderer, encoder, frontend, smoke — in parallel, plus one
Sonnet reviewer. (Per CLAUDE.md: subagents run on Sonnet or Haiku, never Opus
or Fable — heavy renders included.)

## Why

The operator's complaint: chroma key on the GREEN SCREEN export leaves rough,
stair-stepped digit edges no matter how carefully they key it in ProPresenter
or an editor.

The cause, measured on a real rendered frame, not guessed: Pillow
anti-aliases digit glyphs against the green plate, so every edge pixel is a
**blend of green and the digit colour** — one measured edge pixel reads
`(87, 227, 30)`, between the plate's `(0, 255, 0)` and the gold accent's
`(232, 180, 79)`. A 1080p frame carries roughly **3,227** such blended
pixels, and **80.5%** of them carry enough green that a keyer must choose,
pixel by pixel, between keeping a green fringe or cutting a stair-stepped
edge. There is no keyer setting that avoids this trade-off, because the
plate colour is baked into the edge pixel's own RGB value before the keyer
ever sees the frame. This is **not** a resolution problem — a 4K export
anti-aliases the same way, just with more, smaller blended pixels — and not
primarily a 4:2:0 chroma-subsampling problem either, so re-encoding the
green export at a higher resolution or in 4:4:4 would not fix it.

The real fix is to never composite the digits onto a colour at all: keep
each pixel's own alpha (how much of it is "digit" vs "nothing") all the way
through the renderer and the encoder, and hand ProPresenter or the editor a
file that says so — a transparent background, not a green one.

This is possible with zero new dependencies. The bundled imageio-ffmpeg
binary already ships `prores_ks` (Apple ProRes, alpha-capable at profile
4444) and `qtrle` (QuickTime Animation RLE, alpha-capable via
`-pix_fmt argb`) — both confirmed present in this repo's bundled binary
while researching this revision (`ffmpeg -encoders` lists both;
`ffmpeg -h encoder=qtrle` lists `argb` among its four supported pixel
formats). Nothing to download, nothing to pin in `requirements.txt`.

**Real-world testing overturned the original two-format plan.** The first
draft of this spec picked ProRes 4444 and WebM VP9 on the strength of
ProPresenter's and CapCut's documentation, without a real clip in either
app. The owner then actually exported real clips and dropped them into
CapCut, and the result reverses half of that plan: **WebM VP9 alpha
imports into CapCut as a solid white box — the alpha channel is dropped
entirely, not merely degraded.** It is unusable for the one editor this
format existed to serve, and every trace of it is removed from this
revision: the codec table row, its ffmpeg flags (including
`-auto-alt-ref 0`, a flag that only ever meant anything for VP9), its
smoke check, and the "ffmpeg can't decode it, verify with a real player"
caveats that existed solely because of it.

ProRes 4444 imports transparent in CapCut, as hoped. So, unexpectedly,
does `qtrle` (QuickTime Animation, `-c:v qtrle -pix_fmt argb`) — and the
owner judged it side by side against this app's own green-screen-keyed
1080p export as visibly higher quality, with less pixelated digit edges.
Both surviving formats are `.mov` files that both import transparent in
CapCut, so the design constraint is no longer "which app do you use" — it
is file size versus certainty of ProPresenter support, since only ProRes
is on ProPresenter's documented supported-format list (H.264, HEVC,
ProRes variants, HAP); nobody has tested `qtrle` in ProPresenter, and
this spec does not claim it works there. So:

- GREEN SCREEN must stay exactly as it is today — it is the only option
  that works absolutely everywhere, alpha or not.
- TRANSPARENT must offer **both** `.mov` formats and make the operator
  pick, with file size and the ProPresenter caveat both explained in
  plain English before they commit to a choice — `qtrle` is the default
  (smaller, and CapCut-confirmed), ProRes is the one to reach for when the
  file is going straight into ProPresenter.

Measured file sizes: today's H.264 green `.mp4` runs about 51 KB for a
10 s clip at 1920×1080/30fps (roughly 4.6 MB extrapolated to a 15-minute
countdown) — the baseline either alpha format dwarfs. The two alpha
formats were measured differently and more thoroughly this time, because
a size number this consequential needs to survive real content: 210 real
frames captured from the renderer (a 6 s classic countdown with
**milliseconds ticking**, so every frame differs from the last — the
worst case for any compressor), extrapolated to a 15-minute 1080p30
countdown, come out to **ProRes 4444 ≈ 1.6 GB** and **qtrle ≈ 542 MB** —
a third the size, and lossless. Quality tuning does not exist for the
ProRes number: `-qscale:v` 4 / 11 / 20 produced 1641 / 1588 / 1588 MB,
because profile 4444 is intra-only and already near-lossless, so nobody
should spend time trying to tune it smaller later. The 542 MB qtrle
figure is itself a worst case — a plain countdown without ticking
milliseconds has one unique frame per displayed second, which RLE
compresses far harder than a frame that changes 30 times a second. Both
numbers have to reach the operator before they click, not after.

*Correction to earlier framing:* today's code builds the green plate inside
`render/timer.py`'s `_plates()`, not inside `_background()` (`_background()`
only ever builds the plain dark vignette; it has no green-screen or
background-image awareness at all — see `render/timer.py` L94-118 vs the
green-screen branch at L203-204 inside `_plates()`). Every reference below
to "the plate" means `_plates()`.

## Behaviour (what the operator sees)

Background becomes three mutually exclusive choices, all in the existing
**Background** group, all valid in both Timer modes (countdown and clock):
images/plain (today, unchanged), **GREEN SCREEN** (today, unchanged), and
**TRANSPARENT** (new).

| Control | Details |
|---|---|
| TRANSPARENT toggle | Small toggle button, `id="timer-bg-transparent"`, `type="button"`, `aria-pressed="false"` — same shape as `timer-bg-green`, placed right after it in `.bg-strip-wrap`. Clicking it toggles `aria-pressed`. |
| Mutual exclusion | Turning TRANSPARENT on forces GREEN SCREEN's `aria-pressed` back to `"false"`, and vice versa — the two can never both read pressed. (`backgrounds` — the image set — is untouched either way, exactly like green screen today: turning both off again restores whatever image set was already chosen.) |
| Format (shown only while Transparent is on) | Segmented control, two options, default the first: `STANDARD` \| `PRORES`. Reuses the `.seg-group`/`.seg` markup already used for Timer's own MODE control and QR's STYLE control (radio inputs, `class="vh"`, one visible label each) — no new CSS component. |
| Size/format hint (shown only while Transparent is on, updates live with the format choice) | `STANDARD` selected (the default): *"Use this one. Pixel-perfect, and about 180 MB for a 5-minute countdown. Opens in CapCut and other editing software. Not tested in ProPresenter."* `PRORES` selected: *"Only if you are putting the file straight into ProPresenter without editing it first. Bigger — about 530 MB for a 5-minute countdown."* Copy leads with a RECOMMENDATION, not a description: an earlier draft described both neutrally and the owner read it in the browser and still could not tell which to pick. Figures are 5-minute, not 15-minute, because five is the common case and 1.5 MB vs 180 MB lands harder than gigabytes. Never imply STANDARD is lower quality — it is measurably the opposite (qtrle is pixel-exact lossless, max channel error 0; ProRes 4444 is max channel error 1 of 255). |
| Usage hint (shown only while Transparent is on, above the format pair) | *"Only for putting the timer over your own footage in an editor. For a timer that goes straight on screen, leave this off — the normal export is 1.5 MB instead of 180 MB."* Stops someone reaching for transparency on a Sunday when they only want a timer on screen. |
| Format intro line | *"Both save a .mov with a see-through background."* |
| Technical line (`id="timer-transparent-tech"`, `.hint-tech`, subordinate styling) | `STANDARD`: *".mov — QuickTime Animation (RLE), argb, lossless"*. `PRORES`: *".mov — Apple ProRes 4444, yuva444p12le"*. Requested by the owner so an editor can Google the format or check compatibility; deliberately subdued so a volunteer's eye skips it. Shows the PROBED pixfmt, not the encode-time request. |
| Why-this-exists note (`id="timer-transparent-why"`, `.transparent-why-note` callout, **PRORES only**) | *"Why this exists: ProPresenter can play a .mov with a see-through background. Put your own video or image on a layer underneath and it shows through behind the numbers — no editing, and no green screen to key out."* This is the ONLY place the ProPresenter claim is warranted, because ProRes 4444 is in ProPresenter's documented format list and qtrle is not. Never shown for STANDARD. |
| Preview caption + export button | Both hardcode MP4 in the markup and must be corrected while Transparent is on: `#timer-spec-line` reads *"1920x1080 - 30fps - .mov with transparency"* and `#timer-export` reads `EXPORT MOV`. Timer tile only. Both were missed by DOM-level testing and caught by looking at the tile. |
| Image strip, SECONDS PER IMAGE, DIM, BLUR | Hidden while Transparent is on. `+ ADD IMAGE` itself stays visible (owner request, v1.36.0): with it hidden, the only other visible choice was one toggle, so there appeared to be two background options instead of three. Clicking it turns green screen and transparent off, and it shows a gold selected state while images are the background. Previously also hid ADD IMAGE; everything else hidden while Transparent is on — identical rule to Green screen today, just extended to cover the new toggle too. |
| `timer-bg-empty` hint | While Transparent is on: *"Transparent — no background at all. Overlay this file directly on your own video, no keying needed."* (Contrasts on purpose with green screen's own hint, which is the one place this app has ever had to explain keying.) |
| Preview canvas | A checkerboard fill instead of a flat colour, digits/ring/bar drawn on top exactly as usual, no digit shadow halo. **The checkerboard is a preview-only convention** (the universal "this is transparent" signal used by every image/video editor) — it is never written into an exported frame; see Display rules. |

The chosen image set (`timerBg.ids`) and the chosen alpha format are both
*kept* in memory while Transparent (or Green) is on, mirroring green
screen's existing rule — switching back to plain images restores exactly
what was there before.

## Display rules (renderer AND preview must agree)

- The plate is fully transparent, full stop: `RGBA(0, 0, 0, 0)`, 1920×1080.
  No vignette, no dim, no blur, no colour of any kind.
- The style's track (ring track, bar track) paints on top of that plate
  exactly as it does on every other plate today — it is part of the
  graphic, same as it is under green screen.
- Digits, ring, bar, accent, warn colour, AM/PM tag: all drawn exactly as
  today, in the same colours, at the same sizes. **No colour is ever
  blended into the background** — see the Renderer section below for
  exactly how that is enforced, because the codebase's existing paste
  calls get this right in most places and wrong in exactly one place.
- The digit shadow halo (`has_bg`) stays **off**, for the same structural
  reason it is off under green screen: `has_bg` is `bool(options["backgrounds"])`,
  and `backgrounds` normalises to `[]` under Transparent exactly like it
  does under Green screen (see API contract) — so this needs no new
  condition anywhere, the existing mechanism already covers it. It matters
  even more here than it does for green screen: a soft dark halo behind
  the digits would composite as a floating dark smudge with nothing
  under it once the file is dropped over real footage, which is worse
  than merely pointless.
- **The JS preview's checkerboard is presentation only.** The canvas 2D
  context already composites correctly by default (browsers implement
  proper alpha compositing natively — there is no JS equivalent of the
  Pillow bug fixed below), so the preview needs no special compositing
  logic, only the checkerboard fill itself, drawn fresh every redraw. It
  must never be baked into anything the renderer produces.

## Frame rate

**Unchanged.** Transparent export reuses `_input_fps`/`TIMER_OUTPUT_FPS`
and the existing millis-on → 30fps/30fps rule exactly as they are today —
compositing and encoding are orthogonal to timing, and nothing about alpha
changes how many frames are unique or how often the displayed second
changes. A ProRes export is *large*, not *slow to compute*.

## API contract (type stays `"timer"`)

```json
{"type": "timer", "options": {
  "backgrounds": [],
  "green_screen": false,
  "transparent": "qtrle"
}}
```

One new optional key, valid in **both** modes, sitting alongside
`green_screen` in the same Background group:

- `transparent`: `false` (default) or one of `"qtrle"` / `"prores"`. Not a
  plain bool, because there is no single "on" — only a specific format —
  so it is validated against the allowed strings, not `isinstance(x, bool)`.
  Any other value → *`Transparent background must be "qtrle", "prores", or false.`*
- `transparent` truthy **and** `green_screen` truthy together →
  *`Choose either green screen or a transparent background, not both.`*
  (This is a real boundary check, not just a UI nicety — the frontend
  never constructs this combination by construction, see Frontend
  ownership below, but any other API caller could.)
- When `transparent` is truthy, normalised `backgrounds` is forced to `[]`
  regardless of what was sent, and — exactly like green screen — the image
  ids are **not** validated, so a stale id in a hidden set can never block
  a transparent export. `bg_seconds`/`bg_dim`/`bg_blur` are still validated
  and returned as usual (harmless, unused by the renderer under
  Transparent, same as they are unused under Green screen today).
- Allowed values come from one place: `render/encoder.py`'s
  `ALPHA_FORMATS` dict keys (see Encoder section) — `validation.py` imports
  that dict and derives its allowed-values tuple from it, the same way it
  already imports `CLOCK_STYLES` from `render/timer.py`, so the set of
  legal `transparent` strings can never drift out of sync with the set the
  encoder actually knows how to build.
- Filename: the Background group's descriptor suffix becomes a single
  three-way choice instead of the current on/off (`_green` or nothing) —
  `_alpha_qtrle` or `_alpha_prores` or `_green` or nothing, in both
  `render_timer` and `_render_clock`
  (`timer_5m00s_classic_alpha_qtrle_<stamp>.mov`,
  `clock_1959-50_30s_ring_alpha_prores_<stamp>.mov`). Exactly one of these
  can ever be true, since `green_screen` + `transparent` together is
  rejected above.
- File extension: **both formats now share one extension.** `.mov` for
  either `"qtrle"` or `"prores"`, `.mp4` for everything else (unchanged) —
  a simplification the original ProRes/WebM plan did not have.
  `render/encoder.py`'s `export_path(prefix, descriptor, ext=".mp4")`
  already takes an `ext` argument for exactly this (it exists today for
  the QR tile's still PNG) — no change needed to `export_path` itself.
  The renderer still looks `ext` up from `ALPHA_FORMATS[transparent]["ext"]`
  rather than hardcoding `.mov`, so a hypothetical future third alpha
  format in a different container would need no change here either.

## Renderer (`render/timer.py`)

### `_plates()` — one new branch, nothing else changes

```python
def _plates(options, style, accent):
    paths = options.get("backgrounds") or []
    bg_dim = options.get("bg_dim", 45)
    bg_blur = bool(options.get("bg_blur", False))

    if options.get("transparent"):
        # Fully transparent plate (docs/specs/alpha-export.md). The
        # track-painting loop below needs no change for this: it pastes
        # a plain RGB tile through an independently-built 'L' mask, and
        # that specific combination already composites correctly onto
        # an RGBA destination (verified — see _paste_digits below for
        # the one paste pattern in this file that does NOT).
        plates = [Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))]
    elif options.get("green_screen"):
        plates = [Image.new("RGB", (WIDTH, HEIGHT), GREEN_SCREEN)]
    elif paths:
        plates = [prepare_background(p, bg_dim, bg_blur) for p in paths]
    else:
        plates = [_background().copy()]

    for plate in plates:
        if style == "ring":
            plate.paste(Image.new("RGB", (_RING_TILE, _RING_TILE), TRACK),
                        _RING_ORIGIN, _ring_mask(1.0))
        elif style == "bar":
            plate.paste(Image.new("RGB", (BAR_WIDTH, BAR_HEIGHT), TRACK),
                        (BAR_MARGIN, BAR_TOP), _bar_mask(1.0))
    # accent_tile construction: UNCHANGED, stays plain RGB even under
    # Transparent — see "Verified during spec research" below for why
    # that is safe for the ring/bar accent paste in make_frame() too.
    ...
```

### `_paste_digits()` — the one paste that needs a real fix

```python
def _paste_digits(base, block, x, y, has_bg, alpha=False):
    if has_bg:
        halo, pad = _digit_shadow(block.split()[-1])
        black = Image.new("RGB", halo.size, (0, 0, 0))
        base.paste(black, (x - pad, y - pad), halo)
    if alpha:
        # base.paste(block, (x, y), block) below uses `block`'s OWN
        # alpha band as its mask. Pillow blends dest and source using
        # that mask fraction for EVERY band, including alpha itself --
        # which is only correct while the destination is still fully
        # transparent. On an alpha plate the ring/bar TRACK has already
        # painted opaque pixels before digits land, and a semi-
        # transparent glyph-edge pixel pasted this way over an opaque
        # track pixel pulls the result's alpha DOWN toward the glyph's
        # own (lower) alpha instead of staying opaque -- verified with
        # a real Pillow paste: a 50%-alpha source over a fully-opaque
        # destination came back 75% opaque, not 100%. Composited into a
        # real editor that is a faint hole at every glyph edge -- the
        # exact fringe problem alpha export exists to remove, just
        # moved into the alpha channel instead of the colour channel.
        # Image.alpha_composite() implements real Porter-Duff "over"
        # and does not have this problem (verified the same way: the
        # same 50%-alpha source over the same opaque destination stayed
        # 100% opaque). It requires same-size RGBA images, so crop the
        # destination region, composite, paste the (now correctly
        # composited) result straight back with no mask.
        region = base.crop((x, y, x + block.width, y + block.height))
        base.paste(Image.alpha_composite(region, block), (x, y))
        return
    base.paste(block, (x, y), block)
```

Every existing call site keeps its exact 5-argument call (`alpha` defaults
to `False`), so this is byte-identical for all of them. The new alpha
plumbing in `render_timer`/`_render_clock` passes `alpha=is_alpha` at each
of their four `_paste_digits(...)` call sites (the plain `base_for` path
and the millis path, in both the countdown and the clock function), where
`is_alpha = bool(options.get("transparent"))` is computed once near the top
of each function, next to `has_bg`.

**Verified during spec research (not in the original brief — new
findings):** three real Pillow experiments back the two paragraphs above:

1. `Image.paste(rgb_tile, box, l_mask)` (a plain RGB source plus a
   *separately built* `'L'` mask — the pattern every OTHER paste in this
   file uses: the track paint above, and the ring/bar accent paste in
   `make_frame()`) already produces mathematically correct Porter-Duff
   compositing on an RGBA destination's alpha channel, verified against
   `Image.alpha_composite()` at matching pixels — identical results. This
   is why `_plates()`'s track loop and the accent-tile pastes in
   `make_frame()` need **no changes at all** for alpha, and why
   `accent_tile` can stay a plain RGB image even under Transparent.
2. `Image.paste(rgba_block, box, rgba_block)` (an RGBA source used as
   *its own* mask — `_paste_digits`'s only pattern) does **not** match
   `Image.alpha_composite()` at intermediate (anti-aliased) alpha values,
   confirmed with the exact numbers in the code comment above. This is the
   one and only place in `render/timer.py` that needs the crop/composite/
   paste-back fix.
3. The fix costs nothing worth measuring: 200 iterations of
   crop+`alpha_composite`+paste-back on a 300×150 region ran in 19 ms
   total on this machine, against renders that already spend seconds in
   ffmpeg.

### `has_bg` / digit shadow

No new code. `has_bg = bool(options.get("backgrounds"))` already reads
`False` under Transparent, because `backgrounds` is forced to `[]` by
validation — identical mechanism to green screen, see API contract.

### Filename suffix and extension

```python
def _bg_descriptor_suffix(options):
    """At most one of these is ever true — validation.py rejects
    green_screen + transparent together — so this is a plain if/elif
    chain, not independent flags that need combining."""
    transparent = options.get("transparent")
    if transparent == "qtrle":
        return "_alpha_qtrle"
    if transparent == "prores":
        return "_alpha_prores"
    if options.get("green_screen"):
        return "_green"
    return ""
```

Both `render_timer` and `_render_clock` swap their existing inline
`"_green" if options.get("green_screen") else ""` for a call to this one
shared helper (today it is duplicated verbatim between the two functions;
this spec asks the renderer agent to de-duplicate it into one function
while adding the new cases, rather than pasting a third copy of a growing
ternary into each). `export_path(..., ext=...)` takes
`ALPHA_FORMATS[transparent]["ext"]` when `transparent` is truthy (imported
from `render/encoder.py`), `.mp4` otherwise.

### `encode_parallel(...)` call sites

Both functions' existing call —
`encode_parallel(out_path, fps, total_frames, make_frame, progress_cb, output_fps=out_fps)`
— gains one new keyword: `alpha_format=options.get("transparent") or None`.
That is the *entire* renderer-side change needed to reach the encoder;
`make_frame`/`base_for` already hand back RGBA images under Transparent
(because `plates[idx]` is RGBA), and `encode_parallel` just forwards
`alpha_format` straight through to `FrameEncoder` — see Encoder section.

## Encoder (`render/encoder.py`)

### New constant — the one source of truth for both alpha formats

```python
# ---------------------------------------------------------- alpha export
# docs/specs/alpha-export.md. A FIXED codec per format -- chosen for what
# ProPresenter/CapCut can actually open (confirmed by dropping real
# exported clips into CapCut; see Why -- documentation alone said WebM
# would work here and it does not), never probed or picked for speed --
# so this is a completely separate table from _CODEC_FLAGS above, which
# exists ONLY to pick the fastest available H.264 encoder.
ALPHA_FORMATS = {
    "qtrle": {
        # QuickTime Animation (RLE) -- lossless. The default: CapCut-
        # confirmed transparent, and roughly a third the size of ProRes
        # on real timer content (see Why) because RLE compresses the
        # plate/track's large flat runs far harder than ProRes's
        # intra-frame DCT does. NOT on ProPresenter's documented
        # supported-format list (H.264, HEVC, ProRes variants, HAP), and
        # nobody has tested it there -- neither this comment nor the UI
        # copy below may claim it works in ProPresenter.
        "vcodec": "qtrle",
        "flags": [],
        "pix_fmt": "argb",
        "container": "mov",
        "ext": ".mov",
        "movflags": True,
    },
    "prores": {
        # The maximum-compatibility choice: CapCut-confirmed AND on
        # ProPresenter's documented supported-format list. Pick this one
        # whenever the file is going straight into ProPresenter.
        "vcodec": "prores_ks",
        "flags": ["-profile:v", "4444"],
        "pix_fmt": "yuva444p10le",
        "container": "mov",
        "ext": ".mov",
        "movflags": True,   # meaningful for a mov/mp4-family muxer
    },
}
```

### `FrameEncoder.__init__` — one new keyword, existing branch untouched

```python
def __init__(self, out_path, input_fps, width=WIDTH, height=HEIGHT,
            output_fps=OUTPUT_FPS, alpha_format=None):
    self.out_path = out_path
    self._tmp_path = out_path + ".part"
    self.width = width
    self.height = height
    self.frames_written = 0
    self._alpha_format = alpha_format
    self._stderr = tempfile.TemporaryFile()

    if alpha_format is None:
        # UNCHANGED from today, character for character -- do not fold
        # this into a shared table with ALPHA_FORMATS above. Calling
        # pick_codec() once here instead of twice (today's code calls
        # it once for -vcodec and again to index _CODEC_FLAGS) changes
        # nothing observable: pick_codec() is memoized at module level,
        # so both calls already returned the same cached value.
        in_pix_fmt = "rgb24"
        vcodec = pick_codec()
        codec_args = ["-vcodec", vcodec, *_CODEC_FLAGS[vcodec],
                     "-pix_fmt", "yuv420p"]
        container = "mp4"
        movflags = True
    else:
        # Alpha export (docs/specs/alpha-export.md). Deliberately never
        # calls pick_codec()/_probe_codec() or touches _CODEC_FLAGS:
        # pick_codec()'s Windows candidates (h264_nvenc/h264_qsv/
        # h264_amf) are H.264-only hardware encoders that cannot
        # produce alpha at all, _CODEC_FLAGS has no entry for
        # prores_ks/qtrle (a lookup would KeyError), and probing
        # costs a real subprocess spawn (up to a 20s timeout) to
        # "discover" a choice that was never in question -- there is no
        # GPU-accelerated ProRes or RLE encoder to find on a volunteer's
        # laptop, and this path picks its codec from the format the
        # operator chose, not from what is fastest.
        spec = ALPHA_FORMATS[alpha_format]
        in_pix_fmt = "rgba"
        codec_args = ["-vcodec", spec["vcodec"], *spec["flags"],
                     "-pix_fmt", spec["pix_fmt"]]
        container = spec["container"]
        movflags = spec["movflags"]

    cmd = [
        imageio_ffmpeg.get_ffmpeg_exe(),
        "-y",
        "-f", "rawvideo",
        "-pix_fmt", in_pix_fmt,
        "-s", f"{width}x{height}",
        "-r", str(input_fps),
        "-i", "-",
        "-an",
        *codec_args,
        "-r", str(output_fps),
    ]
    if movflags:
        cmd += ["-movflags", "+faststart"]
    cmd += ["-f", container, self._tmp_path]
    # extra = {...}; self._proc = subprocess.Popen(...) -- UNCHANGED.
```

The default branch produces the exact same `cmd` list, in the exact same
order, that today's code builds — this is a reviewer-checkable claim, not
just an assertion (diff the two argument lists directly).

### `add_frame()` — fail loudly instead of silently destroying alpha

```python
def add_frame(self, image):
    if image.size != (self.width, self.height):
        raise EncoderError(
            f"frame is {image.size}, expected {(self.width, self.height)}")
    if self._alpha_format is None:
        if image.mode != "RGB":
            image = image.convert("RGB")
    elif image.mode != "RGBA":
        # A renderer bug, not an operator mistake -- silently converting
        # (today's rule, above) would flatten or discard the alpha this
        # whole feature exists to keep, so this fails loudly instead,
        # the same way the size check above does.
        raise EncoderError(f"alpha frame is {image.mode}, expected RGBA")
    try:
        self._proc.stdin.write(image.tobytes())
    except BrokenPipeError:
        raise EncoderError(
            "ffmpeg exited early: " + self._stderr_tail()) from None
    self.frames_written += 1
```

`image.tobytes()` on a Pillow RGBA image already yields packed RGBA8888,
byte-for-byte what `-pix_fmt rgba` expects on ffmpeg's stdin — no new
packing/marshalling code needed anywhere.

### `encode_parallel(...)` — one new passthrough keyword

```python
def encode_parallel(out_path, input_fps, total_frames, make_frame,
                    progress_cb=None, output_fps=OUTPUT_FPS,
                    alpha_format=None):
    workers = max(1, min(6, (os.cpu_count() or 2) - 1))
    with FrameEncoder(out_path, input_fps, output_fps=output_fps,
                      alpha_format=alpha_format) as enc:
        ...   # UNCHANGED below this line
```

### Verified during spec research (read before implementing)

Both alpha-capable codecs were piped RGBA test frames through *this
repo's bundled* ffmpeg binary before being written into this spec (not
just confirmed present by `-encoders`) — ProRes at the original
1920×1080 frame shape while first researching this spec, `qtrle` at a
smaller synthetic shape while revising it after real CapCut testing ruled
out WebM (see Why) — with results worth knowing before writing the
smoke checks below:

1. **ProRes round-trips through ffmpeg's own decoder correctly, but not at
   the pixel format you asked for.** Encoding with
   `-pix_fmt yuva444p10le` (the only alpha-capable pix_fmt `prores_ks`
   accepts — confirmed via `ffmpeg -h encoder=prores_ks`) and then
   re-probing the file reports the stream as **`yuva444p12le`**, not
   `yuva444p10le` — ProRes 4444's bitstream always carries alpha at
   12-bit internally regardless of the 10-bit input request. Re-extracting
   a frame (`-pix_fmt rgba`) and reading it back with Pillow reproduced
   the exact source pixels, including fractional alpha values, with only
   trivial YUV rounding (a 50%-alpha test pixel came back at 129, not
   128). **Smoke checks must assert the probed pixfmt as
   `"yuva444p12le"`, not the `-pix_fmt` value passed on encode.**
2. **`qtrle` round-trips through ffmpeg's own decoder exactly, at the
   pixel format you asked for, with no rounding at all.** Encoding with
   `-pix_fmt argb` (the only alpha-capable pix_fmt `qtrle` accepts —
   confirmed via `ffmpeg -h encoder=qtrle`, whose full supported list is
   `rgb24 rgb555be argb gray`) and re-probing the file reports the stream
   as **`argb`**, matching the encode request exactly — `qtrle` has no
   separate internal bit depth to round to, the way ProRes does above.
   Confirmed with real RGBA test frames (a 64×32 synthetic frame with a
   fully transparent band, a 50%-alpha band, and a fully opaque band —
   smaller than the 1920×1080 shape the original ProRes/WebM research
   used, but the same codec-level round trip, so the conclusion
   transfers) piped through this repo's bundled ffmpeg while revising
   this spec: re-extracting a frame (`-pix_fmt rgba`) and reading it back
   with Pillow reproduced all three alpha values byte-for-byte — 0, 128,
   and 255 all came back exact, because RLE is lossless and has no YUV
   pipeline to round through. **Smoke checks must assert the probed
   pixfmt as `"argb"` for `qtrle`.** Unlike the WebM plan this revision
   replaces, both alpha formats now round-trip alpha correctly through
   this ffmpeg build's own decoder, so smoke can pixel-verify both — see
   Smoke checks below (including a `probe()` regex gap this surfaced).

## Byte-identical guarantee

Nothing above changes behaviour when `transparent` is absent/`false`:

- `_plates()` gains a new `if` branch that only runs when
  `options.get("transparent")` is truthy; every other branch, and the
  track-painting loop and `accent_tile` construction below it, are
  untouched.
- `_paste_digits()`'s new `alpha` parameter defaults to `False`, and every
  existing call site keeps its current argument count, so every existing
  caller takes the exact `base.paste(block, (x, y), block)` line it always
  took.
- `FrameEncoder.__init__`'s new `alpha_format` parameter defaults to
  `None`, and that branch reconstructs today's exact `cmd` list (see
  Encoder section).
- `add_frame()`'s behaviour when `self._alpha_format is None` is the
  existing "convert to RGB if needed" line, unchanged.

Proof, per CLAUDE.md's standing rule for any renderer change:
`SERVICE_VISUALS_STATS=0 .venv/bin/python scripts/golden.py --check` must
pass unmodified (it hashes decoded pixels of a fixed job list that never
sets `transparent`, so any drift in the untouched path fails it), **and**
the countdown byte-identical recipe: `git show HEAD:render/timer.py` (plus
`encoder.py`, `fonts.py`) into a temp package, render 6 s classic/ring/bar
old vs new with `green_screen` absent, extract frames, `cmp`.

## Analytics

No new prop **key**. `_timer_props()`'s existing `bg` prop (already a
closed, our-own-words enum — `"none"`/`"one"`/`"many"`/`"green"`) gains two
more fixed values:

```python
def _timer_props(options):
    transparent = options.get("transparent")
    if transparent == "qtrle":
        bg = "alpha_qtrle"
    elif transparent == "prores":
        bg = "alpha_prores"
    elif options.get("green_screen"):
        bg = "green"
    else:
        n = len(options.get("backgrounds") or [])
        bg = "none" if n == 0 else ("one" if n == 1 else "many")
    return {"mode": ..., "style": ..., "bg": bg}
```

Same privacy shape as every other value already in `bg`: one of our own
words, never a filename, a count, or anything the operator typed — matches
`stats.py`'s "event name + version + OS only, never content" rule.

## Smoke checks (`scripts/smoke.py`)

Add to the existing `check_clock_validation()` (it already validates
`green_screen` right next to where these belong, reusing its `countdown`/
`clock` fixtures and its local `expect_error` helper):

- `transparent="qtrle"` and `transparent="prores"`, each in both modes:
  `backgrounds` normalises to `[]` even with a bogus id supplied alongside.
- `transparent` omitted defaults to `False`.
- A bad value (e.g. `"png"`) is rejected with the exact message:
  *"Transparent background must be "qtrle", "prores", or false."*
- `transparent="qtrle"` **and** `green_screen=True` together is rejected
  with *"Choose either green screen or a transparent background, not
  both."*

Add a new `check_alpha_export()` (mirroring `check_green_screen()`'s shape
and called from `main()` right after it):

1. **`_plates()` returns a transparent RGBA plate.**
   `_plates({"transparent": "qtrle"}, "ring", (1, 2, 3))` returns exactly
   one plate, mode `"RGBA"`, and pixel `(10, 10)` (well outside the ring)
   reads `(0, 0, 0, 0)`. (Which format string is passed does not matter
   to `_plates()` — it only ever checks truthiness — so this does not
   need a second case for `"prores"`.)
2. **The `_paste_digits` alpha fix, tested directly (not through a full
   render), as a regression guard for the exact bug described above:**
   build a fully-opaque `(35, 38, 43, 255)` RGBA square (stands in for an
   already-painted track pixel), paste a `(242, 240, 235, 128)` RGBA
   square onto it with `alpha=True`, and assert the destination pixel's
   alpha is still `255` — proving the composite did not pull opacity down
   toward the pasted block's own 50%.
3. **Glyph edge alpha is fractional, not binary.** Call `_render_digits`
   (or the clock equivalent) directly for a short string at a real
   render size, and scan its alpha band: assert there is at least one
   fully transparent pixel (0), at least one fully opaque pixel (255),
   **and** at least one strictly-between pixel — proving the glyph is
   anti-aliased, not a hard cutout, exactly the property this whole
   feature depends on.
4. **A real 6 s classic countdown, `transparent: "qtrle"`:** filename ends
   `.mov` and contains `_alpha_qtrle`; `verify(...)` extended with
   `expected_codec="qtrle"`, `expected_pixfmt="argb"` (confirmed by
   actually probing an encode while researching this revision — see the
   verified-during-research note in the Encoder section). Extract frame 0
   with the bundled ffmpeg exactly like `check_green_screen()` does
   (`-frames:v 1 -pix_fmt rgba`, so the extracted PNG keeps its alpha
   band), and assert: a background-area pixel is fully transparent
   (`alpha == 0`); a digit-interior pixel is fully opaque
   (`alpha >= 250`); **and** at least one pixel along a digit's edge
   reads a strictly-between alpha value (`0 < alpha < 255`) — the same
   anti-aliasing property check 3 proves in isolation, now proven to
   survive a real encode-and-decode round trip. This is a genuine
   pixel-level proof, not a container-tag inspection, because `qtrle`
   round-trips alpha through this ffmpeg build's own decoder correctly
   (verified in the Encoder section above) — unlike the WebM path this
   revision removes.
5. **A real 6 s classic countdown, `transparent: "prores"`:** filename
   ends `.mov` and contains `_alpha_prores`; `verify(...)` extended with
   `expected_codec="prores"`, `expected_pixfmt="yuva444p12le"` (see the
   verified-during-research note above — **not** `"yuva444p10le"`, that is
   the encode-time request, not the probed result). Same three pixel
   assertions as check 4 above (transparent background, opaque interior,
   fractional edge) — ProRes alpha round-trips correctly through this
   ffmpeg build too, verified above, so this pixel check is trustworthy
   here as well.
6. **Green screen output is unchanged.** No new check needed — the
   existing `check_green_screen()` must keep passing unmodified; that *is*
   the regression proof for this bullet.

`verify()` gains three optional keyword parameters so every existing call
site (which only ever passes the first three positional arguments) is
untouched:

```python
def verify(name, filename, expected_duration, expected_codec="h264",
          expected_size=(1920, 1080), expected_pixfmt="yuv420p"):
    ...
    check("{0} codec {1}".format(name, expected_codec),
         info["codec"] == expected_codec, ...)
    check("{0} size {1}x{2}".format(name, *expected_size),
         info["size"] == expected_size, ...)
    check("{0} pixfmt {1}".format(name, expected_pixfmt),
         info["pixfmt"] == expected_pixfmt, ...)
    ...
```

Note the check **labels** must interpolate the expected value too (not just
the assertion) — otherwise a passing ProRes check prints a label claiming
`yuv420p` while actually having checked `yuva444p12le`.

**Also required, found while revising this spec, not in the original
brief:** `probe()`'s own regex (`yuv\w+|rgb\w+`) never matches `"argb"` —
verified directly: `re.search(r"yuv\w+|rgb\w+", "argb(progressive)")`
returns `None`, because `"argb"` doesn't start with `"yuv"`, and the
`"rgb"` inside it has nothing after it for `\w+` to require (WebM's
`yuva420p` never hit this gap, only because it happened to start with
`"yuv"`). Add a third alternative — `r"yuv\w+|rgb\w+|argb\w*"` — verified
to still match `"yuva444p12le"` and `"yuv420p"` exactly as before, and to
now also match `"argb"`. Without this fix, check 4's
`expected_pixfmt="argb"` assertion fails on correct encoder output,
because `probe()` can't see its own correct answer, not because anything
is actually wrong.

## Files & ownership (agents edit ONLY their own files)

**Renderer agent** — `render/timer.py`, `validation.py`, `app.py`, `README.md`.
- `render/timer.py`: the `_plates()` branch, the `_paste_digits()` fix, the
  shared `_bg_descriptor_suffix()` helper, the `is_alpha`/`alpha_format`
  locals and the four `_paste_digits(...)` call sites, the
  `encode_parallel(..., alpha_format=...)` keyword at both call sites, the
  `_export_ext()`-style lookup into `ALPHA_FORMATS` for `export_path`'s
  `ext`. Import `ALPHA_FORMATS` from `.encoder` alongside the existing
  `WIDTH, HEIGHT, encode_parallel, export_path` import.
- `validation.py`: `transparent` in `_timer_background_options()` (allowed
  values imported from `render.encoder.ALPHA_FORMATS`'s keys, same pattern
  as the existing `CLOCK_STYLES` import from `render.timer`), the
  green+transparent exclusion check, `transparent` added to the returned
  dict.
- `app.py`: `_timer_props()`'s two new `bg` values. **Also required, found
  while reading this file, not in the original brief:**
  `EXPORT_FILENAME_RE = re.compile(r"[^/\\\x00-\x1f]{1,200}\.(mp4|png|mp3)")`
  only matches `mp4`/`png`/`mp3` — without adding `mov` to that
  alternation, "Reveal in Finder"/"Show file" (`/api/reveal`) will reject
  every alpha export with *"That does not look like the name of an
  exported file."* even though the file rendered successfully. Both alpha
  formats share this one extension, so this is a one-item addition, not
  two. This is the only other file in the app that hardcodes the timer's
  export extensions.
- `README.md`: one clause in the Timer bullet, e.g. "...or **TRANSPARENT**
  for a real alpha-channel export — a smaller `.mov` for CapCut and other
  editors, or the larger ProRes 4444 `.mov` for documented ProPresenter
  compatibility — no keying required."

**Encoder agent** — `render/encoder.py`.
- `ALPHA_FORMATS`, the `FrameEncoder.__init__`/`add_frame` changes, the
  `encode_parallel` passthrough keyword, exactly as specified above. Do
  not touch `pick_codec()`, `_probe_codec()`, or `_CODEC_FLAGS` — the
  alpha path must never call the first two or index the third.

**Frontend agent** — `static/index.html`, `static/js/timer.js`, `static/style.css`.
- `index.html`: the `TRANSPARENT` button (`id="timer-bg-transparent"`,
  same shape as `timer-bg-green`) placed after it in `.bg-strip-wrap`; the
  format `seg-group` (`name="timer-transparent-format"`, ids
  `timer-transparent-format-qtrle`/`-prores` — `qtrle`'s radio `checked`
  by default, matching the Behaviour table's "default the first" rule —
  labels `STANDARD`/`PRORES`) in a group hidden unless
  Transparent is on; a hint paragraph (`id="timer-transparent-hint"`) for
  the live size/format copy.
- `timer.js`:
  - `applyTimerBg()`: extend every place that currently checks `green` to
    check `green || transparent`, plus show/hide the new format
    `seg-group` and update the hint text from the format copy given above.
  - `validateTimerBg()`: the stale-id-count bypass extends to
    `green || transparent`, same reasoning as today's green-only version.
  - `readTimer()`: read `timer-bg-transparent`'s `aria-pressed` and the
    checked format radio into `t.transparent` (`false`/`"qtrle"`/
    `"prores"`); `backgrounds` becomes `[]` when transparent OR green is on.
  - `timerPayload()`: `transparent: t.transparent` alongside
    `green_screen: t.greenScreen` in both mode branches.
  - `drawTimerBackground(ctx, t)`: new first check, before the green
    branch — `if (t.transparent) { drawCheckerboard(ctx); return; }`.
  - New `drawCheckerboard(ctx)`: tiles a two-colour checkerboard (e.g. 20px
    squares, alternating `#ffffff`/`#cccccc` — the same convention every
    image/video editor uses for "this is transparent") across `PW`×`PH`.
    Preview-only; never referenced by anything that produces an exported
    frame.
  - The `timer-bg-green` click handler gains one line: when it is *turning
    on* (was not pressed), also set `timer-bg-transparent`'s `aria-pressed`
    to `"false"`. The new `timer-bg-transparent` click handler is the
    mirror image (same shape as the existing green handler: toggle its own
    `aria-pressed`, force the other button off when turning on, call
    `updateTimer()` — a button click fires no form `input`/`change`,
    exactly as the existing green-handler comment already explains).
  - The frontend never sends `green_screen` and `transparent` both truthy
    — the mutual-exclusion click handlers make that state unreachable
    through the UI — so no new client-side validation message is needed
    for that combination; the backend's rejection above is a boundary
    check for non-UI callers, not something the UI needs its own copy of.
- `style.css`: whatever small rules the new button/seg-group/checkerboard
  need — reuse `.btn`/`.btn-file`/`.seg-group`/`.seg`, no new dependencies.

**Smoke agent** — `scripts/smoke.py`.
- Everything in the Smoke checks section above: the `check_clock_validation()`
  additions, the new `check_alpha_export()` (called from `main()` right
  after `check_green_screen()`), the `verify()` signature change, and the
  `probe()` regex fix (also required, found while revising this spec —
  see Smoke checks section).

## Do not

- Do not let TRANSPARENT change anything about GREEN SCREEN's behaviour
  when TRANSPARENT is never touched — it is a new sibling, not a
  replacement, and it must never activate silently.
- Do not move a single byte of the default (no `transparent` key) output —
  proven by `golden.py --check` plus the countdown byte-identical recipe.
- Do not call `pick_codec()`/`_probe_codec()`, or index `_CODEC_FLAGS`, on
  the alpha path, for any reason.
- Do not bake the JS preview's checkerboard into anything that reaches the
  renderer or the encoder — it is drawn fresh on canvas, every redraw,
  client-side only.
- Do not add bitrate/CRF/quality flags to either `ALPHA_FORMATS` entry —
  the measured file sizes in this spec's Why section assume ffmpeg's own
  defaults for both encoders. For ProRes specifically, this was actually
  tried and measured: `-qscale:v` 4 / 11 / 20 produced 1641 / 1588 / 1588
  MB against the ~1600 MB default, because profile 4444 is intra-only and
  already near-lossless — there is no smaller-but-still-correct ProRes to
  find here, so do not spend time looking for one.
- Do not add a third alpha format, a colour picker for green screen, or a
  resolution change — a 4K export does not fix the problem this feature
  fixes (see Why) and was never on the table.
- Do not claim or imply `qtrle` works in ProPresenter, anywhere — code
  comments, UI copy, or `README.md`. It is unverified there, full stop;
  ProRes is the documented-safe choice for anyone dropping a file straight
  into ProPresenter. If someone later actually tests `qtrle` in
  ProPresenter, that is a follow-up spec update with its own verification,
  not an assumption to bake in now.
- Do not touch the spinner, QR, motion-bg, or scoreboard tiles.
- Do not add dependencies — both codecs are already bundled.
- Do not bump `version.py`, edit `whatsnew.py`, or tag — the orchestrator
  does that.
- Do not touch files outside your ownership list above. If you believe you
  must, stop and say so in your report instead.

## Done means

- `SERVICE_VISUALS_STATS=0 .venv/bin/python scripts/smoke.py` passes,
  including every new check above.
- `SERVICE_VISUALS_STATS=0 .venv/bin/python scripts/golden.py --check`
  passes unmodified.
- `.venv/bin/python -m pyflakes *.py render/*.py scripts/*.py` is clean.
- Orchestrator/reviewer: the byte-identical proof for a no-`transparent`
  6 s countdown in all three styles (recipe above); a real `qtrle` render
  **and** a real ProRes render, each viewed as extracted frames (classic,
  ring, clock) over a contrasting background in something other than
  ffmpeg. No external player needed for either format — unlike the WebM
  plan this revision removes, both formats round-trip alpha correctly
  through this ffmpeg build's own decoder (verified in the Encoder
  section), so extracted frames are trustworthy evidence on their own.
- The UI toggled in the browser: GREEN SCREEN and TRANSPARENT correctly
  exclude each other, the format picker and size hint update live
  (including the `qtrle` hint's "not yet confirmed in ProPresenter" line),
  the checkerboard preview matches what a transparent frame should look
  like.
- `EXPORT_FILENAME_RE` confirmed to accept a real `.mov` filename by
  actually clicking "Reveal in Finder"/"Show file" on an alpha export in a
  running app, not just by reading the regex — one check covers both
  formats, since `qtrle` and `prores` now share the same extension.
