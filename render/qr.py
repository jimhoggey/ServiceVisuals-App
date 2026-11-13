"""QR "scan to..." card renderer.

Builds a static base ONCE (background + one white rounded card holding the
heading, the crisp dark-on-white QR and the caption), then per frame
composites only a soft accent ring that gently breathes OUTSIDE the card.
Everything that matters for a scan is static: the card, the QR, and the quiet
zone never move or scale, so the code stays crisp and readable.

The card is one contained unit (docs/specs/qr-card.md): the owner's heading
used to float above it on the background, where a photo could swallow it.
With no heading and no caption the card is just the square around the code.

Scannability rules (non-negotiable — a code that won't scan is useless):

- The QR is ALWAYS dark modules (#0a0c0e) on a WHITE card. Never inverted;
  many phone scanners refuse light-on-dark codes.
- Modules are drawn BIG. We size the card to the code so modules land around
  MODULE_TARGET px (never below MODULE_MIN), which survives H.264/yuv420p
  softening and reads from across a room and off a projector.
- Error correction is level Q (25%), so partial blur/glare still decodes.
- A quiet zone of QUIET (>=4) white modules surrounds the code.
- Modules are crisp filled squares on exact integer boundaries (no
  anti-aliasing, no dark-module "dot gain" from overlapping rectangles).
- The accent colour touches ONLY the breathing ring (gold text on the white
  card would be unreadable; the text in the card is dark).
- Three `style`s say how the code's modules are drawn: `squares` (default),
  `dots` (filled circles) and `rounded` (soft squares). Finder/alignment
  modules stay square in all three so scanners can still locate the code.
  Never light-on-dark, in any style.

Position and size: the card's centre sits at (`x`, `y`) as fractions of the
frame, clamped so the card is always wholly on screen; `size` scales the
whole card by scaling the module size, which stays a whole number of pixels
and never drops below MODULE_MIN.

Background: the dark or light vignette plate (`backdrop`), or an uploaded
image, cover-fit to 1080p and darkened only as far as `bg_dim` asks.

Seamless loop: total_frames = duration * INPUT_FPS. The ring's opacity/width
follow sin(phase), phase = 2*pi*frame/total_frames, so frame 0 == frame N
(no fade-in, no jump). ProPresenter loops the clip, so a clean wrap is required.
"""

import math
import os
import re

import segno
from segno import consts as _segno_consts
from PIL import Image, ImageDraw, ImageFilter

from . import fonts
from .encoder import (FrameEncoder, HEIGHT, UPLOADS_DIR, WIDTH,
                      export_path)

# Pillow >= 9.1 moved resampling filters into an enum; keep 3.9-safe access.
_RESAMPLING = getattr(Image, "Resampling", Image)
_BILINEAR = _RESAMPLING.BILINEAR
_LANCZOS = _RESAMPLING.LANCZOS

# ---------------------------------------------------------------- palette

BG_BASE = "#0e1013"
BG_EDGE = "#07080a"
TEXT_LIGHT = "#f2f0eb"
CARD_WHITE = "#ffffff"
QR_DARK = "#0a0c0e"
DEFAULT_ACCENT = "#e8b44f"

# The LIGHT backdrop: an off-white plate instead of the dark vignette. It
# was a "style" until the owner pointed out it is a background ("it makes
# the whole screen white"), so it now sits beside DARK and IMAGE.
LIGHT_BASE = "#f4f2ee"
LIGHT_EDGE = "#e6e3dc"
LIGHT_OUTLINE = "#d9d5cc"        # the white card's edge on the light plate
# QR_DARK at 78 % over the white card, as a solid colour: the caption reads
# quieter than the heading, and solid ink leaves the card's alpha alone.
CAPTION_INK = "#404143"

# ---------------------------------------------------------------- QR sizing

QR_ERROR = "q"                  # 25% error correction — robust to blur/glare
QUIET = 4                       # quiet-zone modules of white each side
MODULE_TARGET = 16              # preferred module size (px) — nice and chunky
MODULE_MIN = 9                  # never shrink below this
CARD_PAD = 46                   # white padding between quiet zone and card edge
CARD_MAX = 820                  # cap card side so it fits with heading/caption
CARD_RADIUS = 40

# ---------------------------------------------------------------- layout

EDGE = 40                       # the card stays this far inside the frame

# Sizes at size 100; all scale with the card. The gaps are small because
# the code's own quiet zone already puts 4 modules of white between the
# text and the code.
HEADING_SIZE = 76
CAPTION_SIZE = 42
HEADING_GAP = 10
CAPTION_GAP = 6
HEADING_TRACKING = 6
TEXT_MIN_SCALE = 0.7            # a long line shrinks this far, then the
                                # card widens to hold it

DEFAULT_SIZE = 100
MIN_SIZE, MAX_SIZE = 50, 200    # percent; clamped again to scannable/frame
MAX_BG_DIM = 80

# Ring geometry (outside the card).
RING_GAP = 24
RING_BASE_W = 3.0
RING_SWING_W = 5.0
RING_MIN_A = 40
RING_MAX_A = 150
RING_SS = 2

# ---------------------------------------------------------------- timeline

INPUT_FPS = 15                  # only the ring moves; ffmpeg dupes up to 30
DEFAULT_DURATION = 15
MIN_DURATION = 5
MAX_DURATION = 60

MAX_URL_LEN = 1000
MAX_HEADING_LEN = 30
MAX_CAPTION_LEN = 60

# How the code's modules are drawn (docs/specs/qr-card.md) — always dark on
# the white card; light-on-dark fails on older scanners.
QR_STYLES = ("squares", "dots", "rounded")
DEFAULT_STYLE = "squares"
QR_BACKDROPS = ("dark", "light")
DEFAULT_BACKDROP = "dark"
BG_FITS = ("fill", "fit")       # an uploaded image: crop to fill, or whole


# ---------------------------------------------------------------- helpers

def _hex_rgb(hex_color):
    h = hex_color.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _resolve_background(name):
    """Return an absolute path to an uploaded background, or None.

    Validated to live inside UPLOADS_DIR (defends against traversal even
    though app.py already checks)."""
    if not name:
        return None
    root = os.path.realpath(UPLOADS_DIR)
    path = os.path.realpath(os.path.join(root, name))
    if path.startswith(root + os.sep) and os.path.isfile(path):
        return path
    return None


def _clean_options(options):
    """Defensive re-validation (upstream validates, but never trust it)."""
    if not isinstance(options, dict):
        raise ValueError("qr options must be an object")

    url = str(options.get("url") or "").strip()
    if not (1 <= len(url) <= MAX_URL_LEN):
        raise ValueError(
            "qr url must be 1 to {0} characters".format(MAX_URL_LEN))

    heading = str(options.get("heading") or "").strip()[:MAX_HEADING_LEN]
    caption = str(options.get("caption") or "").strip()[:MAX_CAPTION_LEN]

    accent = options.get("accent") or DEFAULT_ACCENT
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", str(accent)):
        accent = DEFAULT_ACCENT

    def _number(key, default, lo, hi, cast):
        try:
            value = cast(options.get(key, default))
        except (TypeError, ValueError):
            value = default
        return max(lo, min(hi, value))

    x = _number("x", 0.5, 0.0, 1.0, float)
    y = _number("y", 0.5, 0.0, 1.0, float)
    size = _number("size", DEFAULT_SIZE, MIN_SIZE, MAX_SIZE, int)
    bg_dim = _number("bg_dim", 0, 0, MAX_BG_DIM, int)

    style = options.get("style", DEFAULT_STYLE)
    if style not in QR_STYLES:
        style = DEFAULT_STYLE
    backdrop = options.get("backdrop", DEFAULT_BACKDROP)
    if backdrop not in QR_BACKDROPS:
        backdrop = DEFAULT_BACKDROP
    bg_fit = options.get("bg_fit", "fill")
    if bg_fit not in BG_FITS:
        bg_fit = "fill"

    background = _resolve_background(options.get("background"))

    try:
        duration = int(options.get("duration_seconds", DEFAULT_DURATION))
    except (TypeError, ValueError):
        duration = DEFAULT_DURATION
    duration = max(MIN_DURATION, min(MAX_DURATION, duration))

    # The breathing accent ring is the one moving element; off means a
    # plain card and a video of identical frames. Anything but an actual
    # False keeps it, so a missing key renders exactly as before.
    ring = options.get("ring", True) is not False

    return {
        "url": url, "heading": heading, "caption": caption,
        "accent": str(accent), "x": x, "y": y, "size": size,
        "style": style, "backdrop": backdrop, "bg_dim": bg_dim,
        "bg_fit": bg_fit,
        "background": background, "duration": duration, "ring": ring,
    }


def _vignette_background(base_hex=BG_BASE, edge_hex=BG_EDGE):
    """1920x1080 RGB: `base_hex` with a radial vignette to `edge_hex` at the
    edges. Defaults to the dark card backdrop; `light` style reuses this
    with LIGHT_BASE/LIGHT_EDGE instead of duplicating the mask code."""
    small_w, small_h = 240, 135
    base = _hex_rgb(base_hex)
    edge = _hex_rgb(edge_hex)
    img = Image.new("RGB", (small_w, small_h))
    px = img.load()
    cx = (small_w - 1) / 2.0
    cy = (small_h - 1) / 2.0
    max_d = math.hypot(cx, cy)
    for y in range(small_h):
        for x in range(small_w):
            f = (math.hypot(x - cx, y - cy) / max_d) ** 2.0
            px[x, y] = (
                round(base[0] + (edge[0] - base[0]) * f),
                round(base[1] + (edge[1] - base[1]) * f),
                round(base[2] + (edge[2] - base[2]) * f),
            )
    return img.resize((WIDTH, HEIGHT), _BILINEAR)


def _cover(img):
    """Scale to cover 1920x1080 and centre-crop the overflow."""
    iw, ih = img.size
    scale = max(WIDTH / float(iw), HEIGHT / float(ih))
    nw, nh = int(math.ceil(iw * scale)), int(math.ceil(ih * scale))
    img = img.resize((nw, nh), _LANCZOS)
    x = (nw - WIDTH) // 2
    y = (nh - HEIGHT) // 2
    return img.crop((x, y, x + WIDTH, y + HEIGHT))


def _image_background(path, dim, fit="fill"):
    """An uploaded image at 1080p, darkened by `dim` percent.

    `fill` covers the frame and crops what overflows; `fit` shows the whole
    image -- the owner's 3:2 photo lost its "FAITH FUN CONNECTION" line to
    the crop -- with the bars filled by a blurred, darkened copy of the same
    image instead of black, so it still reads as one picture.

    It used to be a fixed 48 % dim so the text above and below the card
    stayed legible; the text is inside the card now, and the owner's image
    may be finished artwork with a gap made for the code, so 0 leaves it
    alone."""
    try:
        img = Image.open(path).convert("RGB")
    except Exception:
        return _vignette_background()
    if fit == "fit":
        iw, ih = img.size
        scale = min(WIDTH / float(iw), HEIGHT / float(ih))
        nw, nh = max(1, int(round(iw * scale))), max(1, int(round(ih * scale)))
        whole = img.resize((nw, nh), _LANCZOS)
        # Blur a small copy (cheap) and darken it, so the bars sit back
        # behind the real picture.
        back = _cover(img.resize((max(1, iw // 8), max(1, ih // 8)),
                                 _BILINEAR))
        back = back.filter(ImageFilter.GaussianBlur(40))
        back = Image.blend(back, Image.new("RGB", (WIDTH, HEIGHT)), 0.35)
        back.paste(whole, ((WIDTH - nw) // 2, (HEIGHT - nh) // 2))
        img = back
    else:
        img = _cover(img)
    if not dim:
        return img
    scrim = Image.new("RGB", (WIDTH, HEIGHT), (0, 0, 0))
    return Image.blend(img, scrim, dim / 100.0)


def _build_background(opts):
    # An uploaded image wins; the card carries its own white field and
    # quiet zone, so any image behind it is safe for the scan.
    if opts["background"]:
        return _image_background(opts["background"], opts["bg_dim"],
                                 opts["bg_fit"])
    if opts["backdrop"] == "light":
        return _vignette_background(LIGHT_BASE, LIGHT_EDGE)
    return _vignette_background()


def _qr_matrix(url):
    """segno matrix as a list of rows of ints (0/1); n = side length, plus
    the segno QRCode itself (dots style reads module types off it)."""
    qr = segno.make(url, error=QR_ERROR)
    matrix = [list(row) for row in qr.matrix]
    return matrix, len(matrix), qr


def _dots_square_mask(qr, n):
    """n x n bools: True where a dark module belongs to a finder or
    alignment pattern, which must stay a solid square so a scanner can
    still locate the code once other data modules become dots.

    matrix_iter(border=0, verbose=True) yields the same n x n grid as
    qr.matrix (checked against a live QRCode: 0 mismatches across every
    module, dark bit vs "is a *_DARK type"), each cell tagged with its
    structural role — this segno version (1.6.6) does expose that verbose
    form, so we use it instead of computing finder squares from position.
    """
    types = list(qr.matrix_iter(scale=1, border=0, verbose=True))
    square = (_segno_consts.TYPE_FINDER_PATTERN_DARK,
              _segno_consts.TYPE_ALIGNMENT_PATTERN_DARK)
    return [[types[r][c] in square for c in range(n)] for r in range(n)]


def _module_px(n):
    """Choose a module size: MODULE_TARGET, shrunk only if the card would
    exceed CARD_MAX, and never below MODULE_MIN."""
    span = n + 2 * QUIET                       # modules across incl. quiet zone
    fit = (CARD_MAX - 2 * CARD_PAD) // span
    return max(MODULE_MIN, min(MODULE_TARGET, int(fit)))


def _text_width(font, text, tracking):
    if not text:
        return 0
    return int(sum(font.getlength(ch) for ch in text)
               + tracking * (len(text) - 1))


def _fit_line(kind, text, size, tracking, room):
    """(font, tracking, width, height) for one line of card text at `size`,
    shrunk toward TEXT_MIN_SCALE if it is wider than `room` -- a long
    heading gets smaller before the card has to get wider."""
    font = fonts.load(kind, max(8, int(round(size))))
    width = _text_width(font, text, tracking)
    if width > room:
        scale = max(TEXT_MIN_SCALE, room / float(width))
        tracking = tracking * scale
        font = fonts.load(kind, max(8, int(size * scale)))
        width = _text_width(font, text, tracking)
    box = font.getbbox(text)
    return font, tracking, width, box[3] - box[1]


def _card_geometry(opts, n, module_px):
    """Sizes of the card and where its three parts sit inside it, for a
    given module size. Everything scales with f = module_px / the default
    module size, so a resized card is the same card, bigger or smaller.

    Stack: pad, heading, code with its quiet zone, caption, pad. A missing
    line takes its gap with it.

    With no text at all the card is "bare": no pad, so its white edge is
    the code's own 4-module quiet zone, and a tighter corner. The owner
    found the padded square too heavy with nothing in it ("the white
    border ... can be smaller ... a bit more modern"). The radius stays
    under 2 modules, so the rounding never reaches a module."""
    f = module_px / float(_module_px(n))
    code_px = module_px * n
    inner = code_px + 2 * QUIET * module_px    # code + quiet zone

    heading = opts["heading"].upper()
    caption = opts["caption"]
    bare = not heading and not caption
    pad = 0 if bare else int(round(CARD_PAD * f))
    radius = (int(round(1.75 * module_px)) if bare
              else int(round(CARD_RADIUS * f)))
    geo = {"module_px": module_px, "code_px": code_px, "f": f,
           "radius": radius,
           "heading": heading, "caption": caption,
           "heading_font": None, "caption_font": None}
    text_w = 0
    y = pad
    if heading:
        font, tracking, width, height = _fit_line(
            "label", heading, HEADING_SIZE * f, HEADING_TRACKING * f, inner)
        geo.update(heading_font=font, heading_tracking=tracking,
                   heading_top=y)
        text_w = max(text_w, width)
        y += height + int(round(HEADING_GAP * f))
    geo["inner_top"] = y
    y += inner
    if caption:
        font, tracking, width, height = _fit_line(
            "caption", caption, CAPTION_SIZE * f, 0, inner)
        y += int(round(CAPTION_GAP * f))
        geo.update(caption_font=font, caption_top=y)
        text_w = max(text_w, width)
        y += height
    geo["width"] = max(inner, text_w) + 2 * pad
    geo["height"] = y + pad
    geo["inner_left"] = (geo["width"] - inner) // 2
    return geo


def _pick_module_px(opts, n):
    """The module size for the asked `size`: whole pixels, never below
    MODULE_MIN (a code that won't scan is useless), and no bigger than
    still fits the frame with EDGE to spare."""
    px = max(MODULE_MIN,
             int(round(_module_px(n) * opts["size"] / 100.0)))
    while px > MODULE_MIN:
        geo = _card_geometry(opts, n, px)
        if geo["width"] <= WIDTH - 2 * EDGE and \
                geo["height"] <= HEIGHT - 2 * EDGE:
            break
        px -= 1
    return px


def _build_card(matrix, n, qr, opts, geo):
    """The card (RGBA): white rounded rectangle, heading, crisp QR, caption.

    `dots` draws each data module as a filled circle and `rounded` as a
    soft square; finder and alignment modules stay square in every style
    (see `_dots_square_mask`)."""
    style = opts["style"]
    module_px = geo["module_px"]
    width, height = geo["width"], geo["height"]

    card = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(card)
    # On the light plate a white card has no edge of its own once the ring
    # is off, so it gets a quiet outline there.
    on_light = opts["backdrop"] == "light" and not opts["background"]
    draw.rounded_rectangle(
        [0, 0, width - 1, height - 1], radius=geo["radius"],
        fill=_hex_rgb(CARD_WHITE) + (255,),
        outline=(_hex_rgb(LIGHT_OUTLINE) + (255,)) if on_light else None,
        width=2 if on_light else 0)

    if geo["heading_font"] is not None:
        _draw_tracked_text(
            draw, width / 2.0, geo["heading_top"], geo["heading"],
            geo["heading_font"], _hex_rgb(QR_DARK) + (255,),
            geo["heading_tracking"])
    if geo["caption_font"] is not None:
        _draw_tracked_text(
            draw, width / 2.0, geo["caption_top"], geo["caption"],
            geo["caption_font"], _hex_rgb(CAPTION_INK) + (255,), 0)

    left = geo["inner_left"] + QUIET * module_px
    top = geo["inner_top"] + QUIET * module_px
    dark = _hex_rgb(QR_DARK)
    square_mask = None if style == "squares" else _dots_square_mask(qr, n)
    soft = max(1, int(module_px * 0.32))
    for r in range(n):
        y0 = top + r * module_px
        for c in range(n):
            if matrix[r][c]:
                x0 = left + c * module_px
                # Exact cell: [x0 .. x0+module_px-1] fills module_px pixels with
                # no overlap into the next cell, so dark and light modules are
                # the same size (no dot gain that would fail a marginal scan).
                box = [x0, y0, x0 + module_px - 1, y0 + module_px - 1]
                if square_mask is None or square_mask[r][c]:
                    draw.rectangle(box, fill=dark)
                elif style == "dots":
                    draw.ellipse(box, fill=dark)
                else:
                    draw.rounded_rectangle(box, radius=soft, fill=dark)
    return card


def _draw_tracked_text(draw, cx, top, text, font, fill, tracking):
    """Horizontally-centered text at glyph-bbox-top `top`, with letter-spacing."""
    total = _text_width(font, text, tracking)
    bbox = font.getbbox(text)
    x = cx - total / 2.0
    y = top - bbox[1]
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        x += font.getlength(ch) + tracking


def _place(centre01, span, frame):
    """Left/top of a `span`-long card centred at `centre01` of `frame`,
    moved in just far enough to stay EDGE inside it -- wherever the card
    is dragged, all of it is on screen. (A code too big for the frame even
    at MODULE_MIN is centred; nothing better can be done with it.)"""
    lo, hi = EDGE, frame - EDGE - span
    if hi < lo:
        return (frame - span) // 2
    return max(lo, min(hi, int(round(centre01 * frame - span / 2.0))))


def _compose_base(opts):
    """Build the static base frame (RGB) and return (base_rgb, geometry).
    geometry gains the card's `left`/`top` in the frame and the `size`
    (percent) actually used, which can differ from the one asked for."""
    matrix, n, qr = _qr_matrix(opts["url"])
    geo = _card_geometry(opts, n, _pick_module_px(opts, n))
    card = _build_card(matrix, n, qr, opts, geo)
    geo["left"] = _place(opts["x"], geo["width"], WIDTH)
    geo["top"] = _place(opts["y"], geo["height"], HEIGHT)
    geo["size"] = int(round(100 * geo["f"]))

    base = _build_background(opts).convert("RGBA")
    base.alpha_composite(card, (geo["left"], geo["top"]))
    return base.convert("RGB"), geo


# The ring only ever occupies a thin band around the card, and its appearance
# depends solely on s = 0.5*(1+sin(phase)). Building a full 3840x2160 overlay
# every frame cost ~86 ms/frame — nearly the entire render. Instead we draw a
# small tile around the card and cache it per quantised s: at RING_STEPS levels
# the alpha moves ~1.7/255 and the width ~0.08 px per step, so the breathe is
# still smooth but a clip needs at most RING_STEPS tiles instead of one per frame.
RING_STEPS = 64
RING_PAD = RING_GAP + int(math.ceil(RING_BASE_W + RING_SWING_W)) + 2


def _ring_tile(geo, step, accent):
    """RGBA tile of the ring for a quantised breathe `step`. Paste it at
    (card_left - RING_PAD, card_top - RING_PAD)."""
    s = step / float(RING_STEPS - 1)
    alpha = int(round(RING_MIN_A + (RING_MAX_A - RING_MIN_A) * s))
    width = RING_BASE_W + RING_SWING_W * s

    box_w = geo["width"] + 2 * RING_PAD
    box_h = geo["height"] + 2 * RING_PAD
    tile = Image.new("RGBA", (box_w * RING_SS, box_h * RING_SS),
                     (0, 0, 0, 0))
    draw = ImageDraw.Draw(tile)

    inset = (RING_PAD - RING_GAP) * RING_SS
    draw.rounded_rectangle(
        [inset, inset, (box_w - (RING_PAD - RING_GAP)) * RING_SS - 1,
         (box_h - (RING_PAD - RING_GAP)) * RING_SS - 1],
        radius=(geo["radius"] + RING_GAP) * RING_SS,
        outline=_hex_rgb(accent) + (alpha,),
        width=max(1, int(round(width * RING_SS))))
    return tile.resize((box_w, box_h), _LANCZOS)


def _ring_step(phase):
    """Quantise the breathe so tiles can be cached."""
    s = 0.5 * (1.0 + math.sin(phase))
    return min(RING_STEPS - 1, max(0, int(round(s * (RING_STEPS - 1)))))


# ---------------------------------------------------------------- renderer

def _ring_pos(geo):
    return (geo["left"] - RING_PAD, geo["top"] - RING_PAD)


def render_qr_preview(options, max_width=900):
    """One representative frame (base + ring near peak) as an RGB PIL image,
    downscaled to max_width, plus where the card is: (frame, box, size).
    `box` is (left, top, width, height) in the full 1920x1080 frame and
    `size` the percent actually used -- the preview draws its drag/resize
    box from them, so a size the renderer clamped is seen, not silent."""
    opts = _clean_options(options)
    base_rgb, geo = _compose_base(opts)
    frame = base_rgb.copy()
    if opts["ring"]:
        tile = _ring_tile(geo, RING_STEPS - 1, opts["accent"])
        frame.paste(tile, _ring_pos(geo), tile)
    if max_width and max_width < WIDTH:
        h = int(HEIGHT * max_width / WIDTH)
        frame = frame.resize((max_width, h), _LANCZOS)
    box = (geo["left"], geo["top"], geo["width"], geo["height"])
    return frame, box, geo["size"]


def render_qr_backdrop(options, max_width=900):
    """Just the background, for the preview before there is anything to
    encode -- the owner uploaded a photo and saw an empty pane until a
    link was typed. `url` is not needed (or looked at)."""
    opts = _clean_options(dict(options, url="-"))
    frame = _build_background(opts)
    if max_width and max_width < WIDTH:
        frame = frame.resize((max_width, int(HEIGHT * max_width / WIDTH)),
                             _LANCZOS)
    return frame


def render_qr_still(options, max_width=900):
    """The preview frame alone: the exact, scannable QR that will be
    exported."""
    return render_qr_preview(options, max_width)[0]


def render_qr_image(options):
    """Export the QR card as a single full-resolution PNG.

    Nothing in this visual actually moves except the accent ring's slow
    breathe, so a still image is usually the more sensible thing to drop into
    ProPresenter (instant, tiny, no clip length to think about). Same 1920x1080
    composition as the video, ring drawn at its peak.
    Returns the filename basename."""
    opts = _clean_options(options)
    frame = render_qr_still(options, max_width=0)     # 0 = keep full 1920x1080
    out_path = export_path("qr", opts["heading"] or "code", ext=".png")
    frame.save(out_path, format="PNG")
    return os.path.basename(out_path)


def render_qr(options, progress_cb):
    """Render the QR "scan to..." card MP4. Returns the filename basename."""
    opts = _clean_options(options)
    base_rgb, geo = _compose_base(opts)

    total_frames = opts["duration"] * INPUT_FPS
    pos = _ring_pos(geo)
    tiles = {}                       # quantised step -> ring tile
    out_path = export_path("qr", opts["heading"] or "code")
    # Only the ring breathes, so 1:1 input->output (15 fps) avoids
    # encoding 2x the frames for no visible gain.
    with FrameEncoder(out_path, INPUT_FPS, output_fps=INPUT_FPS) as enc:
        for k in range(total_frames):
            if not opts["ring"]:
                # Nothing moves: every frame is the base itself.
                enc.add_frame(base_rgb)
                progress_cb(int((k + 1) * 100.0 / total_frames))
                continue
            step = _ring_step(2.0 * math.pi * k / total_frames)
            tile = tiles.get(step)
            if tile is None:
                tile = tiles[step] = _ring_tile(geo, step, opts["accent"])
            frame = base_rgb.copy()
            frame.paste(tile, pos, tile)
            enc.add_frame(frame)
            progress_cb(int((k + 1) * 100.0 / total_frames))
    return os.path.basename(out_path)
