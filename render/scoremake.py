"""Make a scoreboard from scratch, in three looks.

Spec: docs/specs/scoreboard-make.md.

The other scoreboard (render/scoreboard.py) reads a picture of an existing
points screen. This one needs no picture: the operator types team names and
scores and picks a look. It is a still 1920x1080 PNG, rendered
synchronously like the QR still.

Everything is drawn at 2x onto a transparent layer and downscaled ONCE onto
the background, the way qr.py's ring tile is: rounded bars and tile corners
come out crisp instead of stair-stepped, and so does the text.

Coordinates in this file are 1920x1080 ("1x") numbers; only `_Canvas`
knows about the 2x, so a layout tweak never has to think about it.
"""

import math
import os

from PIL import Image, ImageDraw, ImageFilter

from . import fonts
from .encoder import HEIGHT, WIDTH, export_path
from .qr import _image_background, _resolve_background, _vignette_background

SCORE_LOOKS = ("bars", "cards", "podium")
DEFAULT_ACCENT = "#e8b44f"
DEFAULT_DIM = 40
PREVIEW_WIDTH = 900

TEAM_COLORS = ("#e2564b", "#4f8fe6", "#5bbf6a", "#f2c94c",
               "#9b7ede", "#f08a3c", "#3cc3c9", "#e66fb0")

SS = 2                          # supersample factor, see module docstring
TILE_TOP = "#262a31"
TILE_BOTTOM = "#16181d"
INK = "#14161a"                 # dark text on a bright fill
GOLD, SILVER, BRONZE = "#e8b44f", "#c9ced6", "#c98a55"
NEUTRAL = "#3a3f47"
NAME_MIN_SCALE = 0.6            # a long name shrinks this far, then "..."

MARGIN = 110
TITLE_SIZE = 66
TITLE_TRACK = 10


# ---------------------------------------------------------------- colours

def _rgb(hex_color):
    h = hex_color.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _mix(a, b, t):
    """Blend rgb `a` toward rgb `b` by t (0..1)."""
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def _lighter(rgb, t):
    return _mix(rgb, (255, 255, 255), t)


def _darker(rgb, t):
    return _mix(rgb, (0, 0, 0), t)


# ---------------------------------------------------------------- ranking

def order_teams(teams, sort, look):
    """Teams in drawing order, each with `rank` and `leader`.

    Podium is always sorted (it is a leaderboard); the others sort only when
    asked. sorted() is stable, so ties keep the order they were typed in.
    Ranks are competition ranks (1, 1, 3). The leader is whoever holds the
    top score -- every team sharing it -- and nobody while all are 0.
    """
    rows = [dict(t) for t in teams]
    top = max([t["score"] for t in rows] or [0])
    for team in rows:
        team["rank"] = 1 + sum(1 for o in rows if o["score"] > team["score"])
        team["leader"] = top > 0 and team["score"] == top
    if sort or look == "podium":
        return sorted(rows, key=lambda t: -t["score"])
    return rows


# ---------------------------------------------------------------- canvas

class _Canvas(object):
    """A transparent 2x layer with 1x-coordinate drawing helpers."""

    def __init__(self):
        self.img = Image.new("RGBA", (WIDTH * SS, HEIGHT * SS), (0, 0, 0, 0))

    # -- plumbing
    @staticmethod
    def px(v):
        return int(round(v * SS))

    def font(self, role, size):
        return fonts.load(role, max(8, self.px(size)))

    def _box(self, box):
        x0, y0, x1, y1 = [self.px(v) for v in box]
        return x0, y0, max(x0 + 1, x1), max(y0 + 1, y1)

    def _put(self, layer, x, y):
        """alpha_composite `layer` at (x, y), clipped to the canvas."""
        # alpha_composite refuses a negative offset, and a glow's padding
        # can hang over the frame edge.
        cx, cy = max(0, -x), max(0, -y)
        layer = layer.crop((cx, cy, layer.width, layer.height))
        self.img.alpha_composite(layer, (x + cx, y + cy))

    # -- shapes
    def rrect(self, box, radius, fill, outline=None, width=0):
        x0, y0, x1, y1 = self._box(box)
        w, h = x1 - x0, y1 - y0
        layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ImageDraw.Draw(layer).rounded_rectangle(
            [0, 0, w - 1, h - 1], radius=min(self.px(radius), h // 2),
            fill=fill, outline=outline, width=self.px(width))
        self._put(layer, x0, y0)

    def grad_rrect(self, box, radius, top, bottom, alpha=255):
        """Rounded rect with a vertical colour gradient (rgb tuples)."""
        x0, y0, x1, y1 = self._box(box)
        w, h = x1 - x0, y1 - y0
        ramp = Image.linear_gradient("L").resize((w, h))
        body = Image.composite(Image.new("RGB", (w, h), bottom),
                               Image.new("RGB", (w, h), top), ramp)
        mask = Image.new("L", (w, h), 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            [0, 0, w - 1, h - 1], radius=min(self.px(radius), h // 2),
            fill=alpha)
        layer = body.convert("RGBA")
        layer.putalpha(mask)
        self._put(layer, x0, y0)

    def glow(self, box, radius, rgb, blur, alpha):
        """A soft coloured halo behind a rounded rect."""
        x0, y0, x1, y1 = self._box(box)
        pad = self.px(blur) * 3
        w, h = x1 - x0 + 2 * pad, y1 - y0 + 2 * pad
        layer = Image.new("RGBA", (w, h), rgb + (0,))
        ImageDraw.Draw(layer).rounded_rectangle(
            [pad, pad, pad + x1 - x0 - 1, pad + y1 - y0 - 1],
            radius=min(self.px(radius), (y1 - y0) // 2),
            fill=rgb + (alpha,))
        layer = layer.filter(ImageFilter.GaussianBlur(self.px(blur)))
        self._put(layer, x0 - pad, y0 - pad)

    def ellipse(self, box, fill, outline=None, width=0):
        x0, y0, x1, y1 = self._box(box)
        w, h = x1 - x0, y1 - y0
        layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ImageDraw.Draw(layer).ellipse([0, 0, w - 1, h - 1], fill=fill,
                                      outline=outline,
                                      width=self.px(width))
        self._put(layer, x0, y0)

    def star(self, cx, cy, r, fill):
        """A five-point star; drawn rather than typed because "★" is not
        in every machine's fonts and a tofu box on the big screen would be
        worse than no star."""
        pts = []
        for i in range(10):
            ang = -math.pi / 2 + i * math.pi / 5
            rad = r if i % 2 == 0 else r * 0.42
            pts.append((self.px(cx + rad * math.cos(ang)),
                        self.px(cy + rad * math.sin(ang))))
        ImageDraw.Draw(self.img).polygon(pts, fill=fill)

    # -- text
    def text_width(self, font, text, track=0):
        return (font.getlength(text) + self.px(track) * len(text)) / SS

    @staticmethod
    def _cap_half(font):
        """Half the cap height: text is centred on its capitals, so a name
        with a descender and one without sit on the same line."""
        box = font.getbbox("H", anchor="ls")
        return (box[3] - box[1]) / 2.0

    def text(self, x, cy, text, font, fill, anchor="l", track=0):
        """Draw `text` with its capitals centred on y=cy. `anchor` is
        l/m/r for the x position. `track` is extra letter spacing (1x)."""
        width = self.text_width(font, text, track)
        if anchor == "m":
            x -= width / 2.0
        elif anchor == "r":
            x -= width
        base = self.px(cy) + self._cap_half(font)
        draw = ImageDraw.Draw(self.img)
        if not track:
            draw.text((self.px(x), base), text, font=font, fill=fill,
                      anchor="ls")
            return
        pen = self.px(x)
        for ch in text:
            draw.text((pen, base), ch, font=font, fill=fill, anchor="ls")
            pen += font.getlength(ch) + self.px(track)

    def fit(self, role, size, text, max_w, min_scale=NAME_MIN_SCALE,
            track=0):
        """A font at `size`, shrunk until `text` fits `max_w`; past
        `min_scale` the text is cut with "..." instead. -> (font, text)."""
        scale = 1.0
        while True:
            font = self.font(role, size * scale)
            if self.text_width(font, text, track) <= max_w:
                return font, text
            if scale <= min_scale + 1e-6:
                break
            scale = max(min_scale, scale - 0.04)
        cut = text
        while len(cut) > 1 and self.text_width(font, cut + "…",
                                               track) > max_w:
            cut = cut[:-1]
        return font, cut.rstrip() + "…"


def _title(cv, title, accent):
    """The title, accent-coloured and centred. Returns the y where the
    content may start."""
    if not title:
        return 70
    text = title.upper()
    font, text = cv.fit("digits", TITLE_SIZE, text,
                        WIDTH - 2 * MARGIN, 0.6, TITLE_TRACK)
    cv.text(WIDTH / 2.0, 92, text, font, _rgb(accent) + (255,), "m",
            TITLE_TRACK)
    return 190


# ---------------------------------------------------------------- bars

def _draw_bars(cv, teams, opts, top, bottom):
    accent = _rgb(opts["accent"])
    n = len(teams)
    pitch = min((bottom - top) / float(n), 250.0)
    block_top = top + ((bottom - top) - pitch * n) / 2.0
    bar_h = min(pitch * 0.64, 118.0)
    score_size = min(bar_h * 0.86, 104.0)
    name_size = min(bar_h * 0.52, 54.0)
    name_right = 590
    bar_x0 = 630
    right_edge = WIDTH - MARGIN
    score_font = cv.font("digits", score_size)
    tag_font = cv.font("label", 27)
    tag_w = cv.text_width(tag_font, "LEADING", 3) + 44
    widest = max(cv.text_width(score_font, str(t["score"])) for t in teams)
    bar_max = max(300.0, right_edge - bar_x0 - widest - 28 - tag_w - 26)
    top_score = max(t["score"] for t in teams)

    for i, t in enumerate(teams):
        cy = block_top + pitch * (i + 0.5)
        y0, y1 = cy - bar_h / 2.0, cy + bar_h / 2.0
        rgb = _rgb(t["color"])
        # An empty track so a zero still reads as "a bar with no length".
        cv.rrect((bar_x0, y0, bar_x0 + bar_max, y1), bar_h / 2.0,
                 (255, 255, 255, 18))
        frac = t["score"] / float(top_score) if top_score else 0.0
        length = max(bar_max * 0.02 + bar_h * 0.35, bar_max * frac)
        box = (bar_x0, y0, bar_x0 + length, y1)
        if t["leader"]:
            cv.glow(box, bar_h / 2.0, rgb, 22, 150)
        cv.grad_rrect(box, bar_h / 2.0, _lighter(rgb, 0.28),
                      _darker(rgb, 0.12))
        name_font, name = cv.fit("label", name_size, t["name"].upper(),
                                 name_right - MARGIN, track=2)
        shade = 255 if t["leader"] else 205
        cv.text(name_right, cy, name, name_font, (shade,) * 3 + (255,),
                "r", 2)
        sx = bar_x0 + length + 28
        cv.text(sx, cy, str(t["score"]), score_font, (255, 255, 255, 255))
        if t["leader"]:
            tx = sx + cv.text_width(score_font, str(t["score"])) + 26
            cv.rrect((tx, cy - 25, tx + tag_w, cy + 25), 25, accent + (255,))
            cv.text(tx + tag_w / 2.0, cy, "LEADING", tag_font, _rgb(INK) +
                    (255,), "m", 3)


# ---------------------------------------------------------------- cards

def _card_rows(n):
    """Tiles per row: one row up to 4, otherwise two (the top row takes
    the extra one)."""
    if n <= 4:
        return [n]
    top = (n + 1) // 2
    return [top, n - top]


def _draw_cards(cv, teams, opts, top, bottom):
    accent = _rgb(opts["accent"])
    rows = _card_rows(len(teams))
    gap = 36.0
    cols = max(rows)
    avail_w = WIDTH - 2 * MARGIN
    tw = min(520.0, (avail_w - gap * (cols - 1)) / cols)
    area_h = bottom - top
    th = min(640.0, (area_h - gap * (len(rows) - 1)) / len(rows))
    block_h = th * len(rows) + gap * (len(rows) - 1)
    y_start = top + (area_h - block_h) / 2.0
    band = 24.0
    # One numeral size for the whole board, set by the widest score: a
    # 90 drawn bigger than a 340 beside it reads as the bigger number.
    size = min((th - band - 100 - 70) * 0.78, 280.0)
    widest = max((str(t["score"]) for t in teams), key=len)
    score_font, _ = cv.fit("digits", size, widest, tw - 90, 0.25)
    idx = 0
    for r, count in enumerate(rows):
        row_w = tw * count + gap * (count - 1)
        x_start = (WIDTH - row_w) / 2.0
        y0 = y_start + r * (th + gap)
        for c in range(count):
            t = teams[idx]
            idx += 1
            x0 = x_start + c * (tw + gap)
            box = (x0, y0, x0 + tw, y0 + th)
            rgb = _rgb(t["color"])
            radius = 30
            if t["leader"]:
                cv.glow(box, radius, accent, 20, 120)
            # A soft drop shadow lifts the tile off any background.
            cv.glow((x0, y0 + 10, x0 + tw, y0 + th + 10), radius, (0, 0, 0),
                    16, 120)
            cv.grad_rrect(box, radius, _rgb(TILE_TOP), _rgb(TILE_BOTTOM))
            # The band is the tile's own top edge: draw it as a full
            # rounded tile in team colour, then cover everything below the
            # band with the tile again -- keeps the outer corners round.
            _band(cv, box, radius, band, rgb)
            if t["leader"]:
                cv.rrect(box, radius, None, accent + (255,), 6)
            name_font, name = cv.fit("label", 44 if th > 450 else 38,
                                     t["name"].upper(), tw - 64, track=3)
            cv.text(x0 + tw / 2.0, y0 + band + 56, name, name_font,
                    (235, 237, 240, 255), "m", 3)
            # The bottom strip is always reserved so scores line up across
            # tiles whether or not one carries the LEADING label.
            strip = 70.0
            mid_top, mid_bot = y0 + band + 100, y0 + th - strip
            score = str(t["score"])
            cv.text(x0 + tw / 2.0, (mid_top + mid_bot) / 2.0, score,
                    score_font, (255, 255, 255, 255), "m")
            if t["leader"]:
                lab = cv.font("label", 28)
                lw = cv.text_width(lab, "LEADING", 3)
                total = 30 + lw
                sx = x0 + tw / 2.0 - total / 2.0
                cy = y0 + th - strip / 2.0 - 4
                cv.star(sx + 11, cy, 13, accent + (255,))
                cv.text(sx + 30, cy, "LEADING", lab, accent + (255,), "l", 3)


def _band(cv, box, radius, band, rgb):
    """Team-colour band along the top edge of a tile, following its
    rounded corners."""
    x0, y0, x1, y1 = cv._box(box)
    w, h = x1 - x0, y1 - y0
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, w - 1, h - 1], radius=cv.px(radius), fill=255)
    cut = Image.new("L", (w, h), 0)
    ImageDraw.Draw(cut).rectangle([0, 0, w, cv.px(band)], fill=255)
    mask = Image.composite(mask, Image.new("L", (w, h), 0), cut)
    body = Image.composite(Image.new("RGB", (w, h), _darker(rgb, 0.1)),
                           Image.new("RGB", (w, h), _lighter(rgb, 0.25)),
                           Image.linear_gradient("L").resize((w, h)))
    layer = body.convert("RGBA")
    layer.putalpha(mask)
    cv._put(layer, x0, y0)


# ---------------------------------------------------------------- podium

def _medal(rank, scored):
    """Medal colour for a rank; none while the board is all zeros, when
    "1st" would only mean "first in a tie of nobody"."""
    if not scored:
        return NEUTRAL
    return {1: GOLD, 2: SILVER, 3: BRONZE}.get(rank, NEUTRAL)


def _draw_podium(cv, teams, opts, top, bottom):
    accent = _rgb(opts["accent"])
    n = len(teams)
    gap = 18.0
    scored = any(t["score"] > 0 for t in teams)
    firsts = sum(1 for t in teams if t["leader"])
    # The 1st rows are 1.3x taller; capped so two teams do not become two
    # slabs the size of the screen.
    unit = ((bottom - top) - gap * (n - 1)) / (n + 0.3 * firsts)
    unit = min(unit, 150.0)
    heights = [unit * (1.3 if t["leader"] else 1.0) for t in teams]
    block_h = sum(heights) + gap * (n - 1)
    y = top + ((bottom - top) - block_h) / 2.0
    row_w = 1380.0
    x0 = (WIDTH - row_w) / 2.0
    x1 = x0 + row_w
    for t, h in zip(teams, heights):
        cy = y + h / 2.0
        box = (x0, y, x1, y + h)
        lead = t["leader"]
        medal_hex = _medal(t["rank"], scored)
        medal = _rgb(medal_hex)
        if lead:
            cv.glow(box, 26, medal, 18, 90)
            cv.grad_rrect(box, 26, _mix(_rgb(TILE_TOP), medal, 0.30),
                          _mix(_rgb(TILE_BOTTOM), medal, 0.12))
            cv.rrect(box, 26, None, medal + (255,), 4)
        else:
            cv.rrect(box, 26, (22, 24, 28, 215))
        d = h * 0.68
        cx = x0 + 28 + d / 2.0
        cv.ellipse((cx - d / 2.0, cy - d / 2.0, cx + d / 2.0, cy + d / 2.0),
                   medal + (255,))
        # Dark numeral on a bright medal; light on the neutral circle.
        num_ink = "#e6e8ec" if medal_hex == NEUTRAL else INK
        nf = cv.font("digits", d * 0.58)
        cv.text(cx, cy, str(t["rank"]), nf, _rgb(num_ink) + (255,), "m")
        dot_r = h * 0.12
        dx = cx + d / 2.0 + 34 + dot_r
        cv.ellipse((dx - dot_r, cy - dot_r, dx + dot_r, cy + dot_r),
                   _rgb(t["color"]) + (255,))
        score_font = cv.font("digits", h * (0.56 if lead else 0.5))
        score = str(t["score"])
        score_w = cv.text_width(score_font, score)
        name_x = dx + dot_r + 28
        name_room = (x1 - 40 - score_w) - 40 - name_x
        name_font, name = cv.fit("label", h * (0.42 if lead else 0.38),
                                 t["name"].upper(), name_room, track=2)
        cv.text(name_x, cy, name, name_font,
                (255, 255, 255, 255) if lead else (214, 217, 222, 255),
                "l", 2)
        cv.text(x1 - 40, cy, score, score_font,
                accent + (255,) if lead else (255, 255, 255, 255), "r")
        y += h + gap


# ---------------------------------------------------------------- public

_DRAWERS = {"bars": _draw_bars, "cards": _draw_cards,
            "podium": _draw_podium}


def _clean(options):
    """Defensive copy with the defaults filled in (upstream validates)."""
    opts = dict(options)
    opts["look"] = opts.get("look") if opts.get("look") in SCORE_LOOKS \
        else "bars"
    opts["title"] = str(opts.get("title") or "")
    opts["sort"] = opts.get("sort", True) is not False
    opts["accent"] = opts.get("accent") or DEFAULT_ACCENT
    opts["bg_dim"] = opts.get("bg_dim", DEFAULT_DIM)
    opts["background"] = opts.get("background") or ""
    return opts


def _background(opts):
    path = _resolve_background(opts["background"])
    if path:
        return _image_background(path, opts["bg_dim"])
    return _vignette_background()


def render_scoreboard_make(options, max_width=0):
    """The scoreboard as an RGB image: 1920x1080, or `max_width` wide."""
    opts = _clean(options)
    teams = order_teams(opts["teams"], opts["sort"], opts["look"])
    cv = _Canvas()
    top = _title(cv, opts["title"], opts["accent"])
    _DRAWERS[opts["look"]](cv, teams, opts, top, HEIGHT - 80.0)
    layer = cv.img.resize((WIDTH, HEIGHT), Image.LANCZOS)
    frame = _background(opts).convert("RGBA")
    frame.alpha_composite(layer)
    frame = frame.convert("RGB")
    if max_width and max_width < WIDTH:
        frame = frame.resize((max_width, int(round(
            HEIGHT * max_width / float(WIDTH)))), Image.LANCZOS)
    return frame


def export_scoreboard_make(options):
    """Write the PNG into exports/ and return its filename."""
    opts = _clean(options)
    frame = render_scoreboard_make(opts)
    path = export_path("scoreboard", opts["look"], ext=".png")
    frame.save(path, format="PNG")
    return os.path.basename(path)
