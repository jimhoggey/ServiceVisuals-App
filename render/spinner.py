"""Decision wheel renderer.

Draws the full wheel ONCE at 2x supersample (segments, gaps, labels),
then per frame rotates it (BICUBIC), downsamples, and composites onto a
cached vignette background. Hub and pointer are static overlays drawn on
top so they stay crisp instead of shimmering through per-frame resampling.

Angle conventions (the part that must be provably right):

- PIL ImageDraw angles (pieslice): degrees measured from 3 o'clock,
  increasing CLOCKWISE as displayed (y axis points down).
- Image.rotate(a) turns the image COUNTER-clockwise (as displayed) by a
  degrees. "rotation" throughout this module means that CCW angle.
- Segment i is drawn spanning display angles
  [i*seg - 90, (i+1)*seg - 90), so segment 0 begins at the pointer
  (12 o'clock) and segments proceed clockwise.
- After rotating the wheel CCW by r, the wheel angle visible under the
  fixed pointer at display angle -90 is (r - 90), therefore:
      segment_at_pointer(r, n) == floor((r % 360) / (360 / n))
  landing_rotation() is the inverse, aimed at the central 70% of the
  winner's segment so it never lands ambiguously on a boundary and
  rigged results don't look staged by always centering.

The pure helpers segment_at_pointer() / landing_rotation() are the only
place this convention is encoded; everything else just calls them.
"""

import math
import os
import random
import re
from collections import OrderedDict

from PIL import Image, ImageDraw

from . import fonts
from .encoder import (FrameEncoder, HEIGHT, OUTPUT_FPS, WIDTH, export_path)

# Pillow >= 9.1 moved resampling filters into an enum; keep 3.9-safe access.
_RESAMPLING = getattr(Image, "Resampling", Image)
_BICUBIC = _RESAMPLING.BICUBIC
_BILINEAR = _RESAMPLING.BILINEAR
_LANCZOS = _RESAMPLING.LANCZOS

# ---------------------------------------------------------------- visuals

BG_BASE = "#0e1013"
BG_EDGE = "#07080a"
TEXT_LIGHT = "#f2f0eb"
TEXT_DARK = "#101014"
HUB_FILL = "#141619"
CARD_FILL = "#141619"
DEFAULT_ACCENT = "#e8b44f"

PALETTE = [
    "#e8b44f", "#5aa9e6", "#e2725b", "#7fb069", "#9b7ede",
    "#f2c14e", "#4ecdc4", "#e63946", "#f4a261", "#457b9d",
]

CENTER = (WIDTH // 2, HEIGHT // 2)      # (960, 540)
WHEEL_R = 430
HUB_R = 90
HUB_RING = 6
SEG_GAP = 4                              # px gap between segments (1x)
SS = 2                                   # wheel supersample factor
WHEEL_MARGIN = 12                        # 1x margin around the wheel disc
WHEEL_BOX = 2 * (WHEEL_R + WHEEL_MARGIN)          # 1x composited size
WHEEL_BOX2 = SS * WHEEL_BOX                        # 2x drawing size
LABEL_RADIUS_FRAC = 0.62
LABEL_MAX_PX = 46                        # font-size cap at 1x

CARD_CENTER_Y = 880

# ---------------------------------------------------------------- timeline
# The three phases are user-editable (all in whole seconds):
#   wait   — wheel sits still before anything moves (builds anticipation)
#   spin   — wind-up (fixed 0.8s) + the cubic ease-out spin itself
#   winner — how long the winner card stays up at the end
# Defaults reproduce the original fixed 12s-ish clip: 0 + (0.8+7) + 4 = 11.8s.

WINDUP_LEN = 0.8
WINDUP_DEG = -25.0
# Revolutions scale with the spin length so the wheel FEELS the same speed
# whatever the operator sets: a fixed count meant 5 revs crammed into a 2s
# spin (frantic) or stretched over 30s (a crawl). 5 revs per the default 7s,
# never fewer than 2 so even the shortest spin still reads as a real spin.
SPINS_PER_SEC = 5.0 / 7.0
CARD_FADE = 0.4
CARD_DELAY = 0.2          # gap between the wheel settling and the card fading in

WAIT_DEFAULT, WAIT_MIN, WAIT_MAX = 0, 0, 60
SPIN_DEFAULT, SPIN_MIN, SPIN_MAX = 7, 2, 30
WINNER_DEFAULT, WINNER_MIN, WINNER_MAX = 4, 1, 30

MIN_ENTRIES = 2
# 300: the owner's youth conference has 300 people to pick from (was 100).
MAX_ENTRIES = 300
# Above this, slices are too thin for names (see _build_wheel).
LABELS_MAX = 100
MAX_ENTRY_LEN = 40


# ------------------------------------------------------------ landing math

def segment_at_pointer(rotation_deg, n):
    """Index of the segment under the 12-o'clock pointer for a wheel
    rotated `rotation_deg` degrees CCW (Image.rotate convention)."""
    seg = 360.0 / n
    return int((rotation_deg % 360.0) // seg) % n


def landing_rotation(winner_index, n, jitter01):
    """CCW rotation (degrees, in [0, 360)) that parks the pointer inside
    segment `winner_index`. jitter01 in [0, 1) maps to the central 70%
    of the segment: fraction 0.15 .. 0.85, never a boundary."""
    seg = 360.0 / n
    frac = 0.15 + 0.7 * (jitter01 % 1.0)
    return (winner_index + frac) * seg


def segment_colors(n):
    """A palette color per segment; adjacent segments (including the
    wrap-around last/first pair) never share a color. Repeats of the
    palette are offset (by 3 per cycle) with a nudge fix-up so the
    guarantee holds for every n in 2..MAX_ENTRIES."""
    m = len(PALETTE)
    idxs = []
    for i in range(n):
        base = (i + (i // m) * 3) % m
        prev = idxs[-1] if idxs else None
        first = idxs[0] if idxs else None
        pick = base
        for step in range(m):
            cand = (base + step) % m
            if cand == prev:
                continue
            if i == n - 1 and cand == first:
                continue
            pick = cand
            break
        idxs.append(pick)
    return [PALETTE[k] for k in idxs]


# ---------------------------------------------------------------- helpers

def _hex_rgb(hex_color):
    h = hex_color.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _luminance(rgb):
    r, g, b = rgb
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255.0


def _ease_in_out_quad(u):
    if u < 0.5:
        return 2.0 * u * u
    return 1.0 - ((-2.0 * u + 2.0) ** 2) / 2.0


def _ease_out_cubic(u):
    return 1.0 - (1.0 - u) ** 3


def _rotation_at(t, final_rotation, wait, windup_end, spin_end,
                 ease=_ease_out_cubic):
    """Wheel rotation (deg CCW) at time t seconds, for the given timeline:
    still until `wait`, wind-up until `windup_end`, ease-out until `spin_end`."""
    if t <= wait:
        return 0.0
    if t <= windup_end:
        u = (t - wait) / (windup_end - wait)
        return WINDUP_DEG * _ease_in_out_quad(u)
    if t < spin_end:
        u = (t - windup_end) / (spin_end - windup_end)
        return WINDUP_DEG + (final_rotation - WINDUP_DEG) * ease(u)
    return final_rotation


# ------------------------------------------------------------- validation

def _clean_options(options):
    """Defensive re-validation (upstream validates, but never trust it)."""
    if not isinstance(options, dict):
        raise ValueError("spinner options must be an object")

    raw = options.get("entries")
    if not isinstance(raw, (list, tuple)):
        raise ValueError("entries must be a list of names")
    entries = []
    for item in raw:
        text = str(item).strip()[:MAX_ENTRY_LEN]
        if text:
            entries.append(text)
    if not MIN_ENTRIES <= len(entries) <= MAX_ENTRIES:
        raise ValueError(
            "spinner needs {0} to {1} non-empty entries".format(
                MIN_ENTRIES, MAX_ENTRIES))

    mode = options.get("mode", "random")
    if mode not in ("random", "rigged"):
        mode = "random"

    winner = None
    if mode == "rigged":
        winner = str(options.get("winner") or "").strip()[:MAX_ENTRY_LEN]
        if winner not in entries:
            raise ValueError("rigged winner must be one of the entries")

    accent = options.get("accent") or DEFAULT_ACCENT
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", str(accent)):
        accent = DEFAULT_ACCENT

    def _phase(key, default, lo, hi):
        try:
            value = int(options.get(key, default))
        except (TypeError, ValueError):
            value = default
        return max(lo, min(hi, value))

    timing = (
        _phase("wait_seconds", WAIT_DEFAULT, WAIT_MIN, WAIT_MAX),
        _phase("spin_seconds", SPIN_DEFAULT, SPIN_MIN, SPIN_MAX),
        _phase("winner_seconds", WINNER_DEFAULT, WINNER_MIN, WINNER_MAX),
    )
    return entries, mode, winner, accent, timing


# ------------------------------------------------------- cached art layers

def _build_background():
    """1920x1080 RGB: BG_BASE with a radial vignette to BG_EDGE at edges."""
    small_w, small_h = 240, 135
    base = _hex_rgb(BG_BASE)
    edge = _hex_rgb(BG_EDGE)
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


def _label_bounds(seg_deg):
    """(max_w, max_h) available to a label, in 2x (supersampled) pixels."""
    r2 = WHEEL_R * SS
    hub2 = HUB_R * SS
    # Radial run available for the text (2x px).
    max_w = int(r2 - hub2 - 60 * SS)
    # Tangential space near the label band (2x px).
    band = 2.0 * LABEL_RADIUS_FRAC * r2 * math.sin(math.radians(seg_deg) / 2)
    max_h = max(30, min(int(band) - 2 * SEG_GAP * SS, 160))
    return max_w, max_h


def _common_label_size(entries, seg_deg):
    """ONE font size (2x px) that fits EVERY entry.

    Labels used to be sized per entry, so a longer name rendered visibly
    smaller than its neighbours. In choose-winner mode that typographic odd
    one out could telegraph where the wheel lands. A single shared size keeps
    all segments identical, so nothing gives the result away."""
    max_w, max_h = _label_bounds(seg_deg)
    size = LABEL_MAX_PX * SS
    while size > 10 * SS:
        font = fonts.load("label", size)
        ok = True
        for text in entries:
            x0, y0, x1, y1 = font.getbbox(text)
            if x1 - x0 > max_w or y1 - y0 > max_h:
                ok = False
                break
        if ok:
            break
        size -= 2 * SS
    return size


def _label_image(text, seg_color, seg_deg, size):
    """RGBA image of one label, horizontal, at the SHARED font size
    (see _common_label_size). Sizes are in 2x (supersampled) pixels."""
    max_w, _max_h = _label_bounds(seg_deg)
    font = fonts.load("label", size)
    # Last resort at min size: ellipsize.
    shown = text
    x0, y0, x1, y1 = font.getbbox(shown)
    while len(shown) > 1 and x1 - x0 > max_w:
        shown = shown[:-2].rstrip() + "…"
        x0, y0, x1, y1 = font.getbbox(shown)

    color = TEXT_DARK if _luminance(_hex_rgb(seg_color)) > 0.55 else TEXT_LIGHT
    pad = 4
    img = Image.new("RGBA", (x1 - x0 + 2 * pad, y1 - y0 + 2 * pad),
                    (0, 0, 0, 0))
    ImageDraw.Draw(img).text((pad - x0, pad - y0), shown,
                             font=font, fill=color)
    return img


def _build_wheel(entries, colors):
    """The full wheel at 2x as RGBA: segments, transparent 4px gaps
    (background shows through), and radial labels. Rotated per frame."""
    n = len(entries)
    seg = 360.0 / n
    c = WHEEL_BOX2 // 2
    r2 = WHEEL_R * SS
    bbox = [c - r2, c - r2, c + r2, c + r2]

    img = Image.new("RGBA", (WHEEL_BOX2, WHEEL_BOX2), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    for i in range(n):
        draw.pieslice(bbox, i * seg - 90.0, (i + 1) * seg - 90.0,
                      fill=_hex_rgb(colors[i]) + (255,))

    # Labels at ~0.62*R along each mid-angle, rotated to read along the
    # radius. Left-half labels are flipped 180 so no label starts life
    # upside down (they read inward instead). All share one font size so no
    # entry stands out (a size tell could reveal a chosen winner).
    # Past LABELS_MAX the slices are too thin to read (300 names = 1.2 deg
    # each; a test render turned them into white static), so the wheel is
    # colour only and the winner card carries the name. At 100 or fewer
    # nothing changes.
    if n > LABELS_MAX:
        entries = []
    label_size = _common_label_size(entries, seg) if entries else 0
    for i, entry in enumerate(entries):
        label = _label_image(entry, colors[i], seg, label_size)
        theta = math.radians((i + 0.5) * seg - 90.0)
        px = c + LABEL_RADIUS_FRAC * r2 * math.cos(theta)
        py = c + LABEL_RADIUS_FRAC * r2 * math.sin(theta)
        angle = -math.degrees(theta)          # CCW rotate: +x -> outward
        if math.cos(theta) < -1e-9:
            angle += 180.0                    # keep left half upright
        rotated = label.rotate(angle, resample=_BICUBIC, expand=True)
        img.alpha_composite(
            rotated,
            (int(round(px - rotated.width / 2.0)),
             int(round(py - rotated.height / 2.0))))

    # Alpha mask: solid disc minus 4px boundary gaps, so the vignette
    # background shows through between segments.
    mask = Image.new("L", (WHEEL_BOX2, WHEEL_BOX2), 0)
    mdraw = ImageDraw.Draw(mask)
    mdraw.ellipse(bbox, fill=255)
    gap_w = SEG_GAP * SS
    for i in range(n):
        ang = math.radians(i * seg - 90.0)
        end = (c + (r2 + gap_w) * math.cos(ang),
               c + (r2 + gap_w) * math.sin(ang))
        mdraw.line([(c, c), end], fill=0, width=gap_w)
    img.putalpha(mask)
    return img


def _build_hub(accent):
    """Static center hub: HUB_FILL disc with a 6px accent ring (4x AA)."""
    ss = 4
    box = 2 * (HUB_R + 4)
    big = Image.new("RGBA", (box * ss, box * ss), (0, 0, 0, 0))
    draw = ImageDraw.Draw(big)
    m = 4 * ss
    bbox = [m, m, box * ss - m, box * ss - m]
    draw.ellipse(bbox, fill=_hex_rgb(HUB_FILL) + (255,),
                 outline=_hex_rgb(accent) + (255,), width=HUB_RING * ss)
    return big.resize((box, box), _LANCZOS)


def _build_pointer():
    """Crisp downward triangle for 12 o'clock: light fill, subtle dark
    outline (drawn as a slightly inflated dark triangle behind)."""
    ss = 4
    w, h = 76, 62
    pts = [(6.0, 4.0), (70.0, 4.0), (38.0, 56.0)]
    cx = sum(p[0] for p in pts) / 3.0
    cy = sum(p[1] for p in pts) / 3.0

    def scaled(k, dx=0.0, dy=0.0):
        return [((cx + (x - cx) * k + dx) * ss, (cy + (y - cy) * k + dy) * ss)
                for x, y in pts]

    big = Image.new("RGBA", (w * ss, h * ss), (0, 0, 0, 0))
    draw = ImageDraw.Draw(big)
    draw.polygon(scaled(1.10, dy=1.0), fill=(10, 11, 13, 220))   # outline
    draw.polygon(scaled(1.0), fill=_hex_rgb(TEXT_LIGHT) + (255,))
    return big.resize((w, h), _LANCZOS)


def _build_winner_card(winner, accent):
    """Winner card RGBA at full opacity (alpha is scaled during fade-in):
    rounded rect, 95% opaque fill, 2px accent border, letterspaced
    WINNER caption over the winner name. Drawn at 2x for crisp corners."""
    ss = 2
    caption = "WINNER"
    cap_font = fonts.load("caption", 26 * ss)
    tracking = 8 * ss

    name_size = 64
    max_name_w = 1200 * ss
    name_font = fonts.load("label", name_size * ss)
    while name_size > 24:
        name_font = fonts.load("label", name_size * ss)
        nb = name_font.getbbox(winner)
        if nb[2] - nb[0] <= max_name_w:
            break
        name_size -= 2
    nb = name_font.getbbox(winner)
    name_w, name_h = nb[2] - nb[0], nb[3] - nb[1]

    cb = cap_font.getbbox(caption)
    cap_h = cb[3] - cb[1]
    cap_w = int(sum(cap_font.getlength(ch) for ch in caption)
                + tracking * (len(caption) - 1))

    pad_x = 64 * ss
    pad_top = 30 * ss
    gap = 12 * ss
    pad_bot = 34 * ss
    w = max(cap_w, name_w) + 2 * pad_x
    w = max(w, 420 * ss)
    h = pad_top + cap_h + gap + name_h + pad_bot

    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    bw = 2 * ss
    draw.rounded_rectangle(
        [bw // 2, bw // 2, w - 1 - bw // 2, h - 1 - bw // 2],
        radius=18 * ss, fill=_hex_rgb(CARD_FILL) + (242,),   # ~95% opaque
        outline=_hex_rgb(accent) + (255,), width=bw)

    # Letterspaced caption, centered.
    x = (w - cap_w) / 2.0
    y = pad_top - cb[1]
    for ch in caption:
        draw.text((x, y), ch, font=cap_font, fill=_hex_rgb(accent) + (255,))
        x += cap_font.getlength(ch) + tracking

    # Winner name, centered.
    draw.text(((w - name_w) / 2.0 - nb[0], pad_top + cap_h + gap - nb[1]),
              winner, font=name_font, fill=_hex_rgb(TEXT_LIGHT) + (255,))

    return img.resize((w // ss, h // ss), _LANCZOS)


# ------------------------------------------------------- cinematic zoom
# docs/specs/spinner-zoom.md. The owner, with 300 names: "as the spinner
# ... slows down ... zoom in on the picker ... and then the names can
# become more visible." Scaling the wheel bitmap up would blur the names,
# and a 300-name wheel drawn sharp at 12x would be enormous, so the zoomed
# frames are drawn procedurally at the zoomed size: only the slices in
# shot, names rendered at their on-screen size.

# The owner, on the first cut: "Zoom closer. I want to see like 10 names
# total on the big screen and ... it should still be a circle." So the
# close-up frames the top of the rim -- its curve across the top of the
# shot, the slices fanning down from it -- with the names moved out to
# hang from the rim, where the camera is.
ZOOM_NAMES_IN_SHOT = 10.0
ZOOM_MIN, ZOOM_MAX = 1.6, 24.0
# Second cut, the owner: "Why does it first zoom towards the middle and
# then move up? ... it should just naturally zoom in on the top where the
# picker is." The camera now pushes straight in on the top of the rim,
# which glides from where it sits at 1x down to the middle of the
# screen: "the wheel ... occupy like half the screen, and the top half
# is the picker, the big triangle."
RIM_TOP_Y = CENTER[1] - WHEEL_R          # the rim's top at 1x (110)
ZOOM_RIM_SCREEN_Y = HEIGHT / 2.0         # ...and in the close-up
ZOOM_POINTER_MAX = 6.5        # the arrow fills the top half (~400 px)
ZOOM_TIP_INTO_RIM = 20.0      # the arrow's tip ends this far into the rim
POINTER_TIP_Y = 56.0          # the tip's y in _build_pointer's 76x62
ZOOM_FADE_PX = (9.0, 14.0)    # 300-name labels fade in between these sizes
ZOOM_NAME_END_PX = 80.0       # name size in the close-up, at most
ZOOM_NAME_RUN_PX = 440.0      # names run down the bottom half, no further
ZOOM_RIM_PAD_PX = 24.0        # names start this far below the arrow's tip
ZOOM_DIM = 0.62               # the losers' darkening as the winner shows
ZOOM_CARD_TOP = 40            # the card goes above the arrow, in the top
TICK_DEG = 14.0               # the pointer's flick as each name passes
# The ZOOM phase has its own seconds in the timeline ("I want to have a
# spin, but then I also want it to slowly zoom in, but then have some
# suspense build while it's ticking over each of the names"): the first
# ZOOM_PUSH of it is the push-in, the rest the crawl. The crawl starts
# at CRAWL_NAMES_PER_S and slows to a stop on the winner.
ZOOM_S_DEFAULT, ZOOM_S_MIN, ZOOM_S_MAX = 6, 2, 30
ZOOM_PUSH = 0.45
# 5/s: the crawl then passes ~5 names (a cubic tail covers a third of
# speed x time); 2.5 passed under three, too few to build anything.
CRAWL_NAMES_PER_S = 5.0


def zoom_seconds(options):
    """The ZOOM phase's length, defensively clamped like the others."""
    try:
        value = int(options.get("zoom_seconds", ZOOM_S_DEFAULT))
    except (TypeError, ValueError):
        value = ZOOM_S_DEFAULT
    return max(ZOOM_S_MIN, min(ZOOM_S_MAX, value))


def zoom_travel(zoom_s):
    """Slices the wheel passes in the ZOOM phase. Its rotation there eases
    out cubically, 3D(1-u)^2/Z slices/s, so D is chosen to be crossing
    names at CRAWL_NAMES_PER_S when the push-in ends."""
    return CRAWL_NAMES_PER_S * zoom_s / (3.0 * (1.0 - ZOOM_PUSH) ** 2)


def zoom_rotation_at(t, final_rotation, wait, windup_end, spin_end, land,
                     seg):
    """Rotation (deg CCW) with the ZOOM phase: the wind-up as always, then
    the SPIN seconds decelerating evenly into the ZOOM seconds, which ease
    out over the last zoom_travel() slices to stop on the winner. The
    speed is continuous where the two meet, so there's no lurch."""
    if t <= windup_end or t >= land:
        return _rotation_at(t, final_rotation, wait, windup_end, spin_end)
    zoom_s = land - spin_end
    d_b = zoom_travel(zoom_s) * seg
    if t >= spin_end:
        u = (t - spin_end) / zoom_s
        return final_rotation - d_b * (1.0 - u) ** 3
    spin_s = spin_end - windup_end
    d_a = final_rotation - d_b - WINDUP_DEG
    v1 = 3.0 * d_b / zoom_s                  # deg/s into the ZOOM phase
    v0 = 2.0 * d_a / spin_s - v1
    s = t - windup_end
    return WINDUP_DEG + v0 * s + (v1 - v0) * s * s / (2.0 * spin_s)


def zoom_full_spins(full_spins, n, spin_s, zoom_s):
    """Enough whole turns that the SPIN phase still slows down into the
    ZOOM phase (starting faster than it ends), on any wheel size."""
    v1 = 3.0 * zoom_travel(zoom_s) * (360.0 / n) / zoom_s
    while full_spins * 360.0 < 1.5 * v1 * spin_s:
        full_spins += 1
    return full_spins


def _zoom_base_px(entries, seg):
    """Name size (1x px) at zoom 1: the wheel's own shared size up to
    LABELS_MAX; beyond, 70 % of a slice's width at the label radius --
    too small to read until the camera is in close, by design."""
    if len(entries) <= LABELS_MAX:
        return _common_label_size(entries, seg) / float(SS)
    arc = 2 * math.pi * LABEL_RADIUS_FRAC * WHEEL_R / len(entries)
    return max(0.5, 0.7 * arc)


def zoom_max(entries):
    """How far the camera goes in: about ten slices across the width at
    the rim, clamped 1.6x-24x. 300 names zoom ~21x; a 12-name wheel,
    whose ten names are most of it, just leans in."""
    want = WIDTH * len(entries) / (ZOOM_NAMES_IN_SHOT * 2 * math.pi * WHEEL_R)
    return max(ZOOM_MIN, min(ZOOM_MAX, want))


def zoom_window(spin_end, zoom_s):
    """(start, end) seconds of the push-in: the start of the ZOOM phase."""
    return (spin_end, spin_end + ZOOM_PUSH * zoom_s)


def zoom_amount(t, start, end):
    """0 until `start`, eased in-out to 1 at `end`, then held."""
    if t <= start or end <= start:
        return 0.0 if t <= start else 1.0
    return _ease_in_out_quad(min(1.0, (t - start) / (end - start)))


def pointer_flick(pos, speed, since_pass):
    """The pointer's deflection, 0..1 (sign: with the wheel), as slice
    boundaries go by: pushed as one comes up under it, a damped snap
    back once it slips past. `pos` is the wheel position in slices,
    `speed` slices per frame, `since_pass` seconds since the last one
    went under. Still while the wheel is a blur -- a flick every frame
    reads as jitter, not ticks."""
    gain = max(0.0, min(1.0, (0.4 - speed) / 0.25))
    if gain <= 0.0:
        return 0.0
    frac = pos % 1.0
    push = max(0.0, (frac - 0.7) / 0.3)
    snap = 0.0
    if since_pass is not None:
        snap = math.exp(-since_pass / 0.07) * math.cos(
            2 * math.pi * since_pass / 0.22)
    return gain * (push if push > abs(snap) else snap)


def _zoom_label(text, color, px2, max_w2):
    font = fonts.load("label", max(2, int(px2)))
    shown = text
    x0, y0, x1, y1 = font.getbbox(shown)
    if x1 - x0 > max_w2:
        # Shrink a long name to fit before cutting it, to 60 % at most.
        fit = max(int(px2 * 0.6), int(px2 * max_w2 / float(x1 - x0)) - 1)
        font = fonts.load("label", max(2, fit))
        x0, y0, x1, y1 = font.getbbox(shown)
    while len(shown) > 1 and x1 - x0 > max_w2:
        shown = shown[:-2].rstrip() + "…"
        x0, y0, x1, y1 = font.getbbox(shown)
    ink = TEXT_DARK if _luminance(_hex_rgb(color)) > 0.55 else TEXT_LIGHT
    pad = 4
    img = Image.new("RGBA", (x1 - x0 + 2 * pad, y1 - y0 + 2 * pad),
                    (0, 0, 0, 0))
    ImageDraw.Draw(img).text((pad - x0, pad - y0), shown, font=font,
                             fill=ink)
    return img


class _ZoomRenderer(object):
    """Draws one frame of the wheel at zoom Z, camera eased toward the
    names under the pointer. At Z = 1 it matches the bitmap wheel's
    geometry, so the hand-over when the push-in starts doesn't jump."""

    def __init__(self, entries, colors, background, hub, pointer, z_max):
        self.entries, self.colors = entries, colors
        self.n = len(entries)
        self.seg = 360.0 / self.n
        self.background, self.hub, self.pointer = background, hub, pointer
        self.z_max = z_max
        self.base_px = _zoom_base_px(entries, self.seg)
        # Close-up name size: big, but no wider than 45 % of its slice.
        slice_px = 2 * math.pi * WHEEL_R * z_max / self.n
        # ...and never smaller than the wheel's own names, zoomed: on a
        # small wheel the push-in must not shrink them.
        self.end_px = max(min(ZOOM_NAME_END_PX, 0.45 * slice_px),
                          self.base_px * z_max)
        # One size for every name, fitted to the longest, like the wheel
        # itself: shrinking each long name on its own made a ragged row,
        # with "Eliza Stewart 97" cut short beside big short names.
        self.run_end = min(ZOOM_NAME_RUN_PX, (WHEEL_R - HUB_R - 60) * z_max)
        font = fonts.load("label", max(2, int(self.end_px * SS)))
        longest = max(font.getbbox(e)[2] - font.getbbox(e)[0]
                      for e in entries) / float(SS)
        if longest > self.run_end:
            # 0.96: whole-pixel font sizes must not land a hair over.
            self.end_px = max(min(self.end_px, 40.0),
                              0.96 * self.end_px * self.run_end / longest)
        self.labels = OrderedDict()            # (i, px2) -> label RGBA

    def _label(self, i, px2, max_w2):
        # The width is in the key: a name cut short while the room was
        # narrow must not be reused once there is room for all of it.
        key = (i, px2, int(max_w2))
        img = self.labels.get(key)
        if img is None:
            img = _zoom_label(self.entries[i], self.colors[i], px2, max_w2)
            self.labels[key] = img
            while len(self.labels) > 400:
                self.labels.popitem(last=False)
        return img

    def frame(self, rotation, u, flick=0.0, dim=0.0, winner=None):
        # u is the eased push-in, 0..1; z = Z_max ** u reads as an even
        # dolly, and everything else (names, arrow) moves with u too.
        z = self.z_max ** u
        w = u
        cx, cy = CENTER
        # Straight in on the top of the rim: that one world point stays
        # under the arrow, gliding from y=110 down to the middle.
        fx = cx
        fy = RIM_TOP_Y
        sx = WIDTH / 2.0
        sy = RIM_TOP_Y + u * (ZOOM_RIM_SCREEN_Y - RIM_TOP_Y)
        # The arrow grows to fill the top half; its tip, 38 px into the
        # rim at 1x (the bitmap layout), ends ZOOM_TIP_INTO_RIM in.
        s = 1.0 + u * (ZOOM_POINTER_MAX - 1.0)
        tip_in = 38.0 + u * (ZOOM_TIP_INTO_RIM - 38.0)

        def to2(x, y):                         # world (1x) -> layer (2x)
            return ((sx + (x - fx) * z) * SS, (sy + (y - fy) * z) * SS)

        layer = Image.new("RGBA", (WIDTH * SS, HEIGHT * SS), (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        # Which slices are in shot: the frame's corners seen from the
        # centre. Zoomed out (the centre in shot), all of them.
        vx0, vx1 = fx - sx / z, fx + (WIDTH - sx) / z
        vy0, vy1 = fy - sy / z, fy + (HEIGHT - sy) / z
        if vx0 <= cx <= vx1 and vy0 <= cy <= vy1:
            lo, hi = -1e9, 1e9
        else:
            angs = [math.degrees(math.atan2(y - cy, x - cx))
                    for x in (vx0, vx1) for y in (vy0, vy1)]
            # The shot sits above the centre (around -90 deg): unwrap there.
            angs = [((a + 90.0 + 180.0) % 360.0) - 180.0 - 90.0
                    for a in angs]
            lo, hi = min(angs) - self.seg, max(angs) + self.seg

        def visible(a0, a1):
            if lo < -1e8:
                return True
            mid = (((a0 + a1) / 2.0 + 90.0 + 180.0) % 360.0) - 180.0 - 90.0
            return lo <= mid <= hi

        r = WHEEL_R
        step = max(0.25, min(self.seg / 2.0, 12.0 / (r * z) * 57.3))
        shown = []
        for i in range(self.n):
            a0 = i * self.seg - 90.0 - rotation
            a1 = a0 + self.seg
            if not visible(a0, a1):
                continue
            shown.append(i)
            pts = [to2(cx, cy)]
            k = int(math.ceil(self.seg / step))
            for j in range(k + 1):
                a = math.radians(a0 + (a1 - a0) * j / float(k))
                pts.append(to2(cx + r * math.cos(a), cy + r * math.sin(a)))
            draw.polygon(pts, fill=_hex_rgb(self.colors[i]) + (255,))
        # Gaps: a fixed 4 px on screen whatever the zoom, so slices stay
        # slices instead of the gaps swallowing them up close.
        gap = SEG_GAP * SS
        for i in shown + [(shown[-1] + 1) % self.n] if shown else []:
            a = math.radians(i * self.seg - 90.0 - rotation)
            draw.line([to2(cx, cy),
                       to2(cx + (r + 4) * math.cos(a),
                           cy + (r + 4) * math.sin(a))],
                      fill=(0, 0, 0, 0), width=gap)
        # Names, at their on-screen size; past LABELS_MAX only once big
        # enough to read, so a 300-name wheel never shows static.
        # Names grow from the wheel's own size to the close-up size, and
        # slide out from the label radius to hang from the rim.
        px = (self.base_px + w * (self.end_px / self.z_max - self.base_px)) * z
        alpha = 1.0
        if self.n > LABELS_MAX:
            lo_px, hi_px = ZOOM_FADE_PX
            alpha = max(0.0, min(1.0, (px - lo_px) / (hi_px - lo_px)))
        if alpha > 0.0:
            px2 = int(round(px * SS / 2.0)) * 2       # even, for the cache
            room = (WHEEL_R - HUB_R - 60) * z          # the 1x wheel's
            max_w2 = (room + w * (self.run_end - room)) * SS
            for i in shown:
                theta = math.radians((i + 0.5) * self.seg - 90.0)
                phi = theta - math.radians(rotation)
                label = self._label(i, px2, max_w2)
                run = label.width / float(SS)            # on screen, px
                # Below the arrow's tip, so it never hides the winner.
                rim_r = r - (tip_in + ZOOM_RIM_PAD_PX + run / 2.0) / z
                lr = LABEL_RADIUS_FRAC * r + w * (rim_r -
                                                  LABEL_RADIUS_FRAC * r)
                lx, ly = to2(cx + lr * math.cos(phi), cy + lr * math.sin(phi))
                if not (-label.width <= lx <= WIDTH * SS + label.width and
                        -label.width <= ly <= HEIGHT * SS + label.width):
                    continue
                angle = -math.degrees(theta)
                if math.cos(theta) < -1e-9:
                    angle += 180.0
                img = label.rotate(
                    angle + rotation, resample=_BICUBIC, expand=True)
                if alpha < 1.0:
                    img = _faded(img, alpha)
                layer.alpha_composite(
                    img, (int(round(lx - img.width / 2.0)),
                          int(round(ly - img.height / 2.0))))
        frame = self.background.copy()
        small = layer.resize((WIDTH, HEIGHT), _LANCZOS)
        frame.paste(small, (0, 0), small)
        if dim > 0.0 and winner is not None:
            # The reveal: every slice but the winner's sinks into shadow.
            keep = Image.new("L", (WIDTH * SS, HEIGHT * SS), 0)
            a0 = winner * self.seg - 90.0 - rotation
            pts = [to2(cx, cy)]
            for j in range(33):
                a = math.radians(a0 + self.seg * j / 32.0)
                pts.append(to2(cx + r * math.cos(a), cy + r * math.sin(a)))
            ImageDraw.Draw(keep).polygon(pts, fill=255)
            keep = keep.resize((WIDTH, HEIGHT), _LANCZOS)
            shade = keep.point(
                lambda v: int((255 - v) * ZOOM_DIM * dim))
            frame.paste((0, 0, 0), (0, 0), shade)
        # Hub, while any of it is in shot.
        hcx, hcy = sx + (cx - fx) * z, sy + (cy - fy) * z
        hsize = int(round(self.hub.width * z))
        if hcy - hsize / 2.0 < HEIGHT and hsize < 4 * WIDTH:
            hub = self.hub if z == 1.0 else self.hub.resize(
                (hsize, hsize), _LANCZOS)
            frame.paste(hub, (int(round(hcx - hub.width / 2.0)),
                              int(round(hcy - hub.height / 2.0))), hub)
        # The pointer, on the vertical through the centre: the slice under
        # it there is the slice under it at any radius.
        ptr = self.pointer if s == 1.0 else self.pointer.resize(
            (int(round(self.pointer.width * s)),
             int(round(self.pointer.height * s))), _LANCZOS)
        top = sy + tip_in - POINTER_TIP_Y * s
        if not flick:
            frame.paste(ptr, (int(round(sx - ptr.width / 2.0)),
                              int(round(top))), ptr)
            return frame
        # Flicked: pivot on the middle of its top edge, like a flapper.
        # Image.rotate is CCW, which swings the tip right; the top of
        # the wheel moves left (rotation is CCW), and drags the tip.
        d = 2 * max(ptr.width, ptr.height)
        pad = Image.new("RGBA", (d, d), (0, 0, 0, 0))
        pad.paste(ptr, (d // 2 - ptr.width // 2, d // 2))
        pad = pad.rotate(-TICK_DEG * flick, resample=_BICUBIC)
        frame.paste(pad, (int(round(sx - d / 2.0)),
                          int(round(top - d / 2.0))), pad)
        return frame


def _faded(rgba, factor):
    """Copy of an RGBA image with its alpha channel scaled by factor."""
    out = rgba.copy()
    alpha = out.getchannel("A").point(lambda a: int(a * factor))
    out.putalpha(alpha)
    return out


# ---------------------------------------------------------------- renderer

def render_spinner(options, progress_cb):
    """Render the decision wheel MP4. Returns the filename basename."""
    entries, mode, winner, accent, timing = _clean_options(options)
    n = len(entries)

    # User-editable timeline: still -> wind-up+spin -> winner card hold.
    wait_s, spin_s, winner_s = timing
    windup_end = wait_s + WINDUP_LEN
    spin_end = windup_end + spin_s
    # Cinematic zoom (docs/specs/spinner-zoom.md) adds a ZOOM phase after
    # the spin -- push-in, then the crawl -- and the wheel lands at its
    # end. Off: land == spin_end and nothing below changes.
    zoom_on = options.get("zoom") is True
    zoom_s = zoom_seconds(options) if zoom_on else 0
    land = spin_end + zoom_s
    card_in = land + CARD_DELAY
    duration = land + winner_s
    total_frames = int(round(duration * OUTPUT_FPS))

    if mode == "random":
        winner = random.choice(entries)
    winner_index = entries.index(winner)

    jitter = random.random()
    full_spins = max(2, int(round(SPINS_PER_SEC * spin_s)))
    if zoom_on:
        full_spins = zoom_full_spins(full_spins, n, spin_s, zoom_s)
    final_rotation = full_spins * 360.0 + landing_rotation(
        winner_index, n, jitter)
    # Belt and braces: the pure math must agree before we spend render time.
    assert segment_at_pointer(final_rotation % 360.0, n) == winner_index

    background = _build_background()
    # Build the wheel supersampled (crisp labels/edges), then downscale ONCE.
    # Rotating the 1x wheel per frame instead of rotating 2x + downscaling
    # every frame is ~5x faster and visually identical (mean delta < 1%).
    wheel = _build_wheel(entries, segment_colors(n)).resize(
        (WHEEL_BOX, WHEEL_BOX), _LANCZOS)
    hub = _build_hub(accent)
    pointer = _build_pointer()
    card = _build_winner_card(winner, accent)

    wheel_pos = (CENTER[0] - WHEEL_BOX // 2, CENTER[1] - WHEEL_BOX // 2)
    hub_pos = (CENTER[0] - hub.width // 2, CENTER[1] - hub.height // 2)
    pointer_pos = (CENTER[0] - pointer.width // 2,
                   CENTER[1] - WHEEL_R - pointer.height + 44)
    card_pos = (WIDTH // 2 - card.width // 2,
                CARD_CENTER_Y - card.height // 2)

    def compose_base(rotation):
        frame = background.copy()
        spun = wheel.rotate(rotation, resample=_BICUBIC)
        frame.paste(spun, wheel_pos, spun)
        frame.paste(hub, hub_pos, hub)
        frame.paste(pointer, pointer_pos, pointer)
        return frame

    # Cinematic zoom (docs/specs/spinner-zoom.md). Off: nothing below
    # changes -- `zoom_on` is False and every frame is compose_base().
    zoomer = None
    seg = 360.0 / n
    if zoom_on:
        zoomer = _ZoomRenderer(entries, segment_colors(n), background, hub,
                               pointer, zoom_max(entries))
        zoom_t0, zoom_t1 = zoom_window(spin_end, zoom_s)
        # The card goes above the big arrow, clear of the names.
        card_pos = (WIDTH // 2 - card.width // 2, ZOOM_CARD_TOP)

    out_path = export_path("spinner", "{0}items{1}".format(
        n, "_zoom" if zoom_on else ""))
    last_key = None
    prev_rotation = 0.0
    passed_at = None    # when the last slice boundary went under the pointer
    base = None
    settled = None      # fully-faded final frame, reused for the hold
    with FrameEncoder(out_path, OUTPUT_FPS) as enc:
        for k in range(total_frames):
            t = k / float(OUTPUT_FPS)
            if zoom_on:
                rotation = zoom_rotation_at(t, final_rotation, wait_s,
                                            windup_end, spin_end, land, seg)
            else:
                rotation = _rotation_at(t, final_rotation, wait_s,
                                        windup_end, spin_end)
            u = flick = dim = 0.0
            if zoom_on:
                u = zoom_amount(t, zoom_t0, zoom_t1)
                dim = round(max(0.0, min(1.0, (t - card_in) / CARD_FADE)), 3)
                if int(rotation // seg) != int(prev_rotation // seg):
                    passed_at = t
                flick = round(pointer_flick(
                    rotation / seg, (rotation - prev_rotation) / seg,
                    None if passed_at is None else t - passed_at), 3)
                prev_rotation = rotation
            key = (rotation, u, flick, dim)
            if base is None or key != last_key:
                # Before the push-in the fast bitmap wheel is used, zoom on
                # or off; it matches the zoom renderer at 1x.
                base = (zoomer.frame(rotation, u, flick, dim, winner_index)
                        if u > 0.0 else compose_base(rotation))
                last_key = key

            fade = (t - card_in) / CARD_FADE
            if fade <= 0.0:
                frame = base
            elif fade < 1.0:
                frame = base.copy()
                overlay = _faded(card, fade)
                frame.paste(overlay, card_pos, overlay)
            else:
                if settled is None:
                    settled = base.copy()
                    settled.paste(card, card_pos, card)
                frame = settled

            enc.add_frame(frame)
            progress_cb(int((k + 1) * 100.0 / total_frames))

    return os.path.basename(out_path)
