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
4444) and `libvpx-vp9` (alpha-capable WebM) — confirmed present in this
repo's bundled binary while researching this spec. Nothing to download,
nothing to pin in `requirements.txt`.

**The compatibility split is the whole design constraint.** ProPresenter
supports ProRes 4444 with alpha but does not read WebM at all. CapCut reads
WebM VP9 alpha but does not read ProRes 4444 alpha at all. There is no
single alpha file that serves both audiences, so:

- GREEN SCREEN must stay exactly as it is today — it is the only option
  that works absolutely everywhere, alpha or not.
- TRANSPARENT must offer **both** alpha formats and make the operator pick
  the one that matches their own software, with the trade-off (file size,
  in particular) explained in plain English before they commit to a choice.

Measured file sizes, 10 s at 1920×1080/30fps: H.264 green `.mp4` = 51 KB;
WebM VP9 alpha `.webm` = 91 KB; ProRes 4444 alpha `.mov` = 16 MB.
Extrapolated to a 15-minute countdown: roughly 4.6 MB (green), 8 MB (WebM),
**1.4 GB** (ProRes). That 1.4 GB has to reach the operator before they
click, not after.

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
| Format (shown only while Transparent is on) | Segmented control, two options, default the first: `PROPRESENTER` \| `CAPCUT & EDITORS`. Reuses the `.seg-group`/`.seg` markup already used for Timer's own MODE control and QR's STYLE control (radio inputs, `class="vh"`, one visible label each) — no new CSS component. |
| Size/format hint (shown only while Transparent is on, updates live with the format choice) | `PROPRESENTER` selected: *"Saves a .mov file for ProPresenter. Large — about 16 MB per 10 seconds (roughly 1.4 GB for a 15-minute countdown)."* `CAPCUT & EDITORS` selected: *"Saves a .webm file for CapCut and most editors. Much smaller — about 91 KB per 10 seconds (roughly 8 MB for a 15-minute countdown). ProPresenter cannot open this one."* Names the app first, the file extension second, never the codec name — a volunteer needs to know what it opens with and how big it will be, not what a "profile 4444" is. |
| Image strip, `+ ADD IMAGE`, SECONDS PER IMAGE, DIM, BLUR | Hidden while Transparent is on — identical rule to Green screen today, just extended to cover the new toggle too. |
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
  "transparent": "prores"
}}
```

One new optional key, valid in **both** modes, sitting alongside
`green_screen` in the same Background group:

- `transparent`: `false` (default) or one of `"prores"` / `"webm"`. Not a
  plain bool, because there is no single "on" — only a specific format —
  so it is validated against the allowed strings, not `isinstance(x, bool)`.
  Any other value → *`Transparent background must be "prores", "webm", or false.`*
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
  `_alpha_prores` or `_alpha_webm` or `_green` or nothing, in both
  `render_timer` and `_render_clock`
  (`timer_5m00s_classic_alpha_prores_<stamp>.mov`,
  `clock_1959-50_30s_ring_alpha_webm_<stamp>.webm`). Exactly one of these
  can ever be true, since `green_screen` + `transparent` together is
  rejected above.
- File extension follows the format: `.mov` for `"prores"`, `.webm` for
  `"webm"`, `.mp4` for everything else (unchanged). `render/encoder.py`'s
  `export_path(prefix, descriptor, ext=".mp4")` already takes an `ext`
  argument for exactly this (it exists today for the QR tile's still PNG)
  — no change needed to `export_path` itself, only to what the timer
  renderer passes for `ext`.

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
    if transparent == "prores":
        return "_alpha_prores"
    if transparent == "webm":
        return "_alpha_webm"
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
# ProPresenter/CapCut can open, never probed or picked for speed -- so
# this is a completely separate table from _CODEC_FLAGS above, which
# exists ONLY to pick the fastest available H.264 encoder.
ALPHA_FORMATS = {
    "prores": {
        "vcodec": "prores_ks",
        "flags": ["-profile:v", "4444"],
        "pix_fmt": "yuva444p10le",
        "container": "mov",
        "ext": ".mov",
        "movflags": True,   # meaningful for a mov/mp4-family muxer
    },
    "webm": {
        "vcodec": "libvpx-vp9",
        # Disables VP9 alt-ref/lookahead frames. Documented, widely-hit
        # failure mode: an alt-ref frame has no paired alpha frame, and
        # on longer real content (unlike this spec's short verification
        # clips, which never triggered it) that can corrupt the alpha
        # channel on exactly those frames. Costs nothing measurable on
        # the file sizes this spec is built around.
        "flags": ["-auto-alt-ref", "0"],
        "pix_fmt": "yuva420p",
        "container": "webm",
        "ext": ".webm",
        "movflags": False,  # -movflags is a mov/mp4-muxer option; webm
                            # ignores it, so it is simply not passed
                            # rather than passed-and-ignored
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
        # prores_ks/libvpx-vp9 (a lookup would KeyError), and probing
        # costs a real subprocess spawn (up to a 20s timeout) to
        # "discover" a choice that was never in question -- there is no
        # GPU ProRes/VP9 encoder to find on a volunteer's laptop, and
        # this path picks its codec from the format the operator chose,
        # not from what is fastest.
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

### Verified during spec research (not in the original brief — read before implementing)

Both codecs were actually piped 1920x1080-shaped RGBA test frames through
*this repo's bundled* ffmpeg binary while researching this spec (not just
confirmed present by `-encoders`), with two results worth knowing before
writing the smoke checks below:

1. **ProRes round-trips through ffmpeg's own decoder correctly, but not at
   the pixel format you asked for.** Encoding with
   `-pix_fmt yuva444p10le` (the only alpha-capable pix_fmt `prores_ks`
   accepts — confirmed via `ffmpeg -h encoder=prores_ks`) and then
   re-probing the file reports the stream as **`yuva444p12le`**, not
   `yuva444p10le` — ProRes 4444's bitstream always carries alpha at
   12-bit internally regardless of the 10-bit input request. Re-extracting
   a frame (`-pix_fmt rgba`) and reading it back with Pillow reproduced
   the exact source pixels, including fractional alpha values, with only
   trivial YUV rounding. **Smoke checks must assert the probed pixfmt as
   `"yuva444p12le"`, not the `-pix_fmt` value passed on encode.**
2. **The bundled ffmpeg cannot decode VP9-in-WebM alpha back out through
   its own CLI, even from a correctly-encoded file.** Probing an encoded
   WebM with `ffmpeg -i` shows the muxer *did* write the correct
   `alpha_mode : 1` container tag, but every attempt to re-extract a
   frame as RGBA (with or without `-auto-alt-ref 0`, with or without an
   explicit bitrate) came back with alpha = 255 everywhere — a false
   negative, not a real bug: the **same file**, opened in a real
   standards-compliant decoder (Chrome, via a `<video>` element sampled
   pixel-by-pixel with `drawImage`+`getImageData`), reproduced the exact
   source alpha values, fractional edges included. This is a known,
   documented limitation of ffmpeg's own VP9 alpha *decoder* (encoding
   support is fine); it is not something a code change in this app can
   fix, and it is not evidence the feature is broken. **Smoke cannot
   verify WebM alpha by extracting a frame with ffmpeg** — see Smoke
   checks below for what it checks instead, and Done means for how a
   human actually confirms it.

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
    if transparent == "prores":
        bg = "alpha_prores"
    elif transparent == "webm":
        bg = "alpha_webm"
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

- `transparent="prores"` and `transparent="webm"`, each in both modes:
  `backgrounds` normalises to `[]` even with a bogus id supplied alongside.
- `transparent` omitted defaults to `False`.
- A bad value (e.g. `"png"`) is rejected with the exact message.
- `transparent="prores"` **and** `green_screen=True` together is rejected
  with *"Choose either green screen or a transparent background, not
  both."*

Add a new `check_alpha_export()` (mirroring `check_green_screen()`'s shape
and called from `main()` right after it):

1. **`_plates()` returns a transparent RGBA plate.**
   `_plates({"transparent": "prores"}, "ring", (1, 2, 3))` returns exactly
   one plate, mode `"RGBA"`, and pixel `(10, 10)` (well outside the ring)
   reads `(0, 0, 0, 0)`.
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
4. **A real 6 s classic countdown, `transparent: "prores"`:** filename
   ends `.mov` and contains `_alpha_prores`; `verify(...)` extended with
   `expected_codec="prores"`, `expected_pixfmt="yuva444p12le"` (see the
   verified-during-research note above — **not** `"yuva444p10le"`, that is
   the encode-time request, not the probed result); extract frame 0 with
   the bundled ffmpeg exactly like `check_green_screen()` does, and assert
   a background-area pixel is fully transparent (`alpha == 0`) while a
   digit-interior pixel is fully opaque (`alpha >= 250`) — ProRes alpha
   round-trips correctly through this ffmpeg build, verified above, so
   this pixel check is trustworthy.
5. **A real 6 s classic countdown, `transparent: "webm"`:** filename ends
   `.webm` and contains `_alpha_webm`; `verify(...)` with
   `expected_codec="vp9"`. **Do not** extend this to a pixel-alpha check
   via ffmpeg extraction — per the verified note above, this ffmpeg
   build's own decoder cannot read VP9 alpha back out even from a
   correctly-encoded file, so that assertion would fail on *correct* code
   and is not a real signal. Instead assert the muxer's own container tag
   is present: run `ffmpeg -i <path>` (same subprocess shape `probe()`
   already uses) and check the literal text `"alpha_mode"` appears in
   stderr — that confirms the encoder wrote the right signal without
   depending on this ffmpeg build's decoder being able to read it back.
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
`yuv420p` while actually having checked `yuva444p12le`. `probe()`'s own
regex (`yuv\w+|rgb\w+`) needs no change at all — verified directly, it
already captures `"yuva444p12le"` and `"yuv420p"` correctly as-is.

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
  only matches `mp4`/`png`/`mp3` — without adding `mov` and `webm` to that
  alternation, "Reveal in Finder"/"Show file" (`/api/reveal`) will reject
  every alpha export with *"That does not look like the name of an
  exported file."* even though the file rendered successfully. This is the
  only other file in the app that hardcodes the timer's export extensions.
- `README.md`: one clause in the Timer bullet, e.g. "...or **TRANSPARENT**
  for a real alpha-channel export — ProRes 4444 (`.mov`) for ProPresenter,
  or the much smaller WebM VP9 (`.webm`) for CapCut and other editors — no
  keying required."

**Encoder agent** — `render/encoder.py`.
- `ALPHA_FORMATS`, the `FrameEncoder.__init__`/`add_frame` changes, the
  `encode_parallel` passthrough keyword, exactly as specified above. Do
  not touch `pick_codec()`, `_probe_codec()`, or `_CODEC_FLAGS` — the
  alpha path must never call the first two or index the third.

**Frontend agent** — `static/index.html`, `static/js/timer.js`, `static/style.css`.
- `index.html`: the `TRANSPARENT` button (`id="timer-bg-transparent"`,
  same shape as `timer-bg-green`) placed after it in `.bg-strip-wrap`; the
  format `seg-group` (`name="timer-transparent-format"`, ids
  `timer-transparent-format-prores`/`-webm`, labels `PROPRESENTER`/
  `CAPCUT & EDITORS`) in a group hidden unless Transparent is on; a hint
  paragraph (`id="timer-transparent-hint"`) for the live size/format copy.
- `timer.js`:
  - `applyTimerBg()`: extend every place that currently checks `green` to
    check `green || transparent`, plus show/hide the new format
    `seg-group` and update the hint text from the format copy given above.
  - `validateTimerBg()`: the stale-id-count bypass extends to
    `green || transparent`, same reasoning as today's green-only version.
  - `readTimer()`: read `timer-bg-transparent`'s `aria-pressed` and the
    checked format radio into `t.transparent` (`false`/`"prores"`/
    `"webm"`); `backgrounds` becomes `[]` when transparent OR green is on.
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
  after `check_green_screen()`), and the `verify()` signature change.

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
- Do not add bitrate/CRF/quality flags to either `ALPHA_FORMATS` entry
  beyond what is specified above — the measured file sizes in this spec's
  Why section assume ffmpeg's own defaults for these two encoders; tuning
  them is a follow-up with its own measurements, not part of this spec.
- Do not add a third alpha format, a colour picker for green screen, or a
  resolution change — a 4K export does not fix the problem this feature
  fixes (see Why) and was never on the table.
- Do not try to make the WebM smoke check assert on ffmpeg-decoded pixel
  alpha. That is a documented limitation of this ffmpeg build's own VP9
  decoder, not a bug in this app; "fixing" it by changing encode flags
  until the ffmpeg round-trip check passes would be chasing a phantom and
  could easily make the real (encoder-side) behaviour worse while the
  check goes green.
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
  6 s countdown in all three styles (recipe above); a real ProRes render
  viewed as extracted frames (classic, ring, clock) over a contrasting
  background in something other than ffmpeg; a real WebM render **opened
  in an actual player that supports WebM alpha** (e.g. a `<video>` element
  in a real browser tab, sampled with canvas `getImageData`, or dropped
  into CapCut/Chrome directly) rather than "checked" through ffmpeg's own
  CLI, which cannot see it — do not sign off on WebM alpha from a
  ffmpeg-only check, per the verified limitation above.
- The UI toggled in the browser: GREEN SCREEN and TRANSPARENT correctly
  exclude each other, the format picker and size hint update live, the
  checkerboard preview matches what a transparent frame should look like.
- `EXPORT_FILENAME_RE` confirmed to accept a real `.mov`/`.webm` filename
  by actually clicking "Reveal in Finder"/"Show file" on one of each in a
  running app, not just by reading the regex.
