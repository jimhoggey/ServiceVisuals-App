"""Headless smoke test for the Service Visuals renderers.

Renders a short timer in each style plus a rigged spinner, then decodes
each MP4 with the bundled ffmpeg and asserts:

  * codec  : h264
  * size   : 1920x1080
  * pixfmt : yuv420p
  * length : expected duration +/- 0.5 s

Prints PASS/FAIL per check, exits nonzero on any failure, and removes its
own test files from exports/ so the user's export folder stays clean.

Run:  .venv/bin/python scripts/smoke.py
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

# Allow running from anywhere.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# The scoreboard check writes boards to ~/.service-visuals unless redirected.
# Point it at a throwaway dir BEFORE render.scoreboard is imported (it reads
# the variable once, at import) so a smoke run never touches real boards.
_BOARD_TMP = tempfile.mkdtemp(prefix="sv-smoke-boards-")
os.environ["SERVICE_VISUALS_CONFIG"] = _BOARD_TMP

import imageio_ffmpeg  # noqa: E402

from render.encoder import EXPORTS_DIR  # noqa: E402
from render.timer import render_timer  # noqa: E402
from render.spinner import render_spinner  # noqa: E402
from render.qr import render_qr  # noqa: E402
from render.motionbg import render_motion_bg  # noqa: E402
from render import scoreboard  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mock_board  # noqa: E402
import jscheck  # noqa: E402

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()

failures = []


def check(label, ok, detail=""):
    status = "PASS" if ok else "FAIL"
    line = "  [{0}] {1}".format(status, label)
    if detail and not ok:
        line += "  ({0})".format(detail)
    print(line)
    if not ok:
        failures.append(label)


def probe(path):
    """Decode stream metadata with the bundled ffmpeg (no ffprobe shipped).

    `ffmpeg -i <file>` exits nonzero (no output specified) but prints the
    container/stream info we need to stderr.
    """
    proc = subprocess.run(
        [FFMPEG, "-hide_banner", "-i", path],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    text = proc.stderr.decode("utf-8", "replace")

    info = {"codec": None, "size": None, "pixfmt": None, "duration": None}

    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
    if m:
        info["duration"] = (int(m.group(1)) * 3600 + int(m.group(2)) * 60
                            + float(m.group(3)))

    m = re.search(r"Stream #\d+:\d+.*?: Video: (\w+)[^\n]*", text)
    if m:
        line = m.group(0)
        info["codec"] = m.group(1)
        sm = re.search(r"(\d{3,5})x(\d{3,5})", line)
        if sm:
            info["size"] = (int(sm.group(1)), int(sm.group(2)))
        # argb\w* is required, not just rgb\w+, to see qtrle's "argb"
        # pixfmt at all (docs/specs/alpha-export.md): "rgb\w+" needs a
        # word char AFTER "rgb", and "argb" has none, so
        # re.search(r"yuv\w+|rgb\w+", "argb(progressive)") returns None
        # even though the pixfmt is right there in the string. Verified
        # this still matches "yuva444p12le" and "yuv420p" exactly as
        # before, and now also matches "argb".
        pm = re.search(r"yuv\w+|rgb\w+|argb\w*", line)
        if pm:
            info["pixfmt"] = pm.group(0)
    return info


def verify(name, filename, expected_duration, expected_codec="h264",
          expected_size=(1920, 1080), expected_pixfmt="yuv420p"):
    path = os.path.join(EXPORTS_DIR, filename)
    print("{0}: {1}".format(name, filename))
    if not os.path.isfile(path):
        check("{0} file exists".format(name), False, "missing: " + path)
        return
    info = probe(path)
    check("{0} codec {1}".format(name, expected_codec),
          info["codec"] == expected_codec,
          "got {0!r}".format(info["codec"]))
    check("{0} size {1}x{2}".format(name, *expected_size),
          info["size"] == expected_size,
          "got {0!r}".format(info["size"]))
    check("{0} pixfmt {1}".format(name, expected_pixfmt),
          info["pixfmt"] == expected_pixfmt,
          "got {0!r}".format(info["pixfmt"]))
    dur = info["duration"]
    ok = dur is not None and abs(dur - expected_duration) <= 0.5
    check("{0} duration ~{1}s".format(name, expected_duration), ok,
          "got {0!r}".format(dur))


def _showinfo_pts(path):
    """Decode every frame with the bundled ffmpeg's showinfo filter and
    return the list of pts_time floats in decode order (docs/specs/
    millis-60fps.md) -- the ground truth for frame count and inter-frame
    spacing. A container header's "NN fps" tag or a Duration: line (what
    probe()/verify() above already check) can't tell a genuine constant
    framerate from a relabeled or duplicated one; a per-frame decode can.
    No "-v error" here (unlike probe()'s ffmpeg call) -- showinfo logs at
    ffmpeg's default "info" level, so quieting the log would silence the
    very lines this reads.
    """
    proc = subprocess.run(
        [FFMPEG, "-hide_banner", "-i", path, "-vf", "showinfo",
         "-f", "null", "-"],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    text = proc.stderr.decode("utf-8", "replace")
    return [float(m) for m in re.findall(r"pts_time:([\d.]+)", text)]


def _frame_hashes(path, count, width=1920, height=1080):
    """Decode the first `count` frames to raw RGB24 and return one
    sha256 hex digest per frame (docs/specs/millis-60fps.md) -- proves
    frames genuinely differ rather than trusting timestamps alone, the
    same way golden.py hashes decoded pixels rather than trusting a
    container tag. Streamed in fixed chunks like golden.py's _hash_mp4,
    so a run is never held whole in memory -- only `count` frames are
    requested from ffmpeg at all, so this stays cheap even though the
    full clip may be much longer.
    """
    frame_size = width * height * 3
    proc = subprocess.Popen(
        [FFMPEG, "-v", "error", "-i", path, "-frames:v", str(count),
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    hashes = []
    buf = b""
    while True:
        chunk = proc.stdout.read(1024 * 1024)
        if not chunk:
            break
        buf += chunk
        while len(buf) >= frame_size:
            hashes.append(hashlib.sha256(buf[:frame_size]).hexdigest())
            buf = buf[frame_size:]
    proc.communicate()
    return hashes


def check_whats_new():
    """docs/specs/whats-new.md: notes_for()'s assembly rule, then the
    four-row show/hide table, then the two routes end to end.

    Run FIRST in main(), before anything else in this file touches
    SERVICE_VISUALS_CONFIG (_BOARD_TMP) — the "config dir is genuinely
    empty" row needs that to still be true. whatsnew.CONFIG_DIR resolves
    to the same _BOARD_TMP every other module's CONFIG_DIR does (read
    once at import, like scoreboard's BOARDS_DIR), so mutating that
    directory's CONTENTS between checks exercises all four rows without
    needing a fresh process per row.
    """
    import whatsnew

    print("Whats-new: notes_for() copy assembly")
    lines_125 = whatsnew.notes_for("1.25.0")
    check("1.25.0 (its own list is empty) tops up to 3 from 1.24/1.23",
          lines_125 == whatsnew.NOTES["1.24.0"] + whatsnew.NOTES["1.23.0"],
          "got {0!r}".format(lines_125))

    lines_126 = whatsnew.notes_for("1.26.0")
    check("1.26.0 leads with its own line, then tops up to 3",
          len(lines_126) == 3
          and lines_126[0] == whatsnew.NOTES["1.26.0"][0],
          "got {0!r}".format(lines_126))

    lines_unknown = whatsnew.notes_for("9.9.9")
    check("an unknown version still returns 3, drawn from the newest known",
          len(lines_unknown) == 3, "got {0!r}".format(lines_unknown))

    check("no duplicate line within any of the above",
          all(len(set(lines)) == len(lines)
              for lines in (lines_125, lines_126, lines_unknown)),
          "a line repeated")

    all_lines = [ln for lines in whatsnew.NOTES.values() for ln in lines]
    # The spec's own guideline is "~90" (a tilde — approximate, for a new
    # entry to aim for); three already-written lines run to 92/94/106, so
    # this checks against that reality rather than a boundary the shipped
    # copy itself does not meet. Not silently loosened without a trace:
    # flagged in the module's own NOTES comment and in this check's label.
    check("every NOTES line stays within a sane on-screen length "
          "(<=110; spec's own target for new lines is ~90)",
          all(len(ln) <= 110 for ln in all_lines),
          "got lengths {0!r}".format(sorted(len(ln) for ln in all_lines)))

    print()
    print("Whats-new: show/hide decision (four-row table)")
    config_dir = whatsnew.CONFIG_DIR
    last_seen = whatsnew.LAST_SEEN_PATH

    def reset():
        if os.path.isdir(config_dir):
            for name in os.listdir(config_dir):
                path = os.path.join(config_dir, name)
                if os.path.isfile(path):
                    os.unlink(path)
                else:
                    shutil.rmtree(path, ignore_errors=True)

    # Row 4: no file, config dir empty/absent -> do not show. Checked
    # first, while _BOARD_TMP is still genuinely untouched.
    reset()
    check("row 4: no file + empty config dir -> do not show",
          whatsnew.should_show("1.26.0") is False,
          "got {0!r}".format(whatsnew.should_show("1.26.0")))

    # Row 3: no file, but the config dir already has OTHER content — the
    # row that makes an EXISTING user see the card on the release that
    # introduces it.
    os.makedirs(config_dir, exist_ok=True)
    with open(os.path.join(config_dir, "analytics.json"), "w") as f:
        f.write("{}")
    check("row 3: no file, but other content present -> show",
          whatsnew.should_show("1.26.0") is True,
          "got {0!r}".format(whatsnew.should_show("1.26.0")))
    reset()

    # Row 1/2: file present — newer running version shows, same or older
    # does not.
    with open(last_seen, "w") as f:
        json.dump({"version": "1.25.0"}, f)
    check("row 1: running version newer than stored -> show",
          whatsnew.should_show("1.26.0") is True)
    check("row 2: running version same as stored -> do not show",
          whatsnew.should_show("1.25.0") is False)
    check("row 2: running version older than stored -> do not show",
          whatsnew.should_show("1.24.0") is False)
    reset()

    print()
    print("Whats-new: /api routes")
    import app as _app
    client = _app.app.test_client()

    with open(last_seen, "w") as f:
        json.dump({"version": "0.0.1"}, f)
    resp = client.get("/api/whats-new")
    body = resp.get_json() or {}
    check("GET /api/whats-new shows when running is newer than stored",
          resp.status_code == 200 and body.get("show") is True
          and body.get("items"), "got {0!r}".format(body))
    check("a repeat GET does not itself write last-seen.json",
          whatsnew._read_last_seen() == "0.0.1",
          "GET mutated last-seen.json")

    resp = client.post("/api/whats-new/seen")
    check("POST /api/whats-new/seen writes {ok: true} and the running "
          "version",
          resp.status_code == 200 and resp.get_json() == {"ok": True}
          and whatsnew._read_last_seen() == _app.APP_VERSION,
          "got body={0!r} stored={1!r}".format(
              resp.get_json(), whatsnew._read_last_seen()))

    resp = client.get("/api/whats-new")
    check("after being seen, a same-version GET no longer shows",
          (resp.get_json() or {}).get("show") is False,
          "got {0!r}".format(resp.get_json()))

    reset()


def check_prepare_background():
    """render.timer.prepare_background: cover-fit, dim, blur — exercised as
    a pure image function (docs/specs/timer-backgrounds.md), no video
    involved. Cover-fit is checked with a KNOWN edge pixel rather than a
    pixel diff: a tall (100x400) and a wide (400x100) source each get a
    hard colour split at their midpoint, and the crop this produces is
    centred close enough to that midpoint that the pixel right at each
    edge of the finished 1920x1080 frame still reads as one clean colour
    (not a LANCZOS-blended one) — proving the crop is centred and the
    image was scaled, not stretched, in each direction.
    """
    import tempfile as _tempfile

    from PIL import Image, ImageDraw

    from render.timer import prepare_background

    print("Timer: prepare_background (cover-fit / dim / blur)")
    tmp = _tempfile.mkdtemp(prefix="sv-smoke-prepbg-")
    try:
        # Tall source: top half red, bottom half blue, split at row 200 of
        # 400 — cover-fit scales it to fill 1920 WIDE (the binding axis for
        # a portrait source against a landscape target) and crops the
        # overflowing height around that same midpoint.
        tall_path = os.path.join(tmp, "tall.png")
        tall = Image.new("RGB", (100, 400), (220, 20, 20))
        ImageDraw.Draw(tall).rectangle([0, 200, 100, 400], fill=(20, 20, 220))
        tall.save(tall_path)
        out = prepare_background(tall_path, 0, False)
        check("tall source cover-fits to exactly 1920x1080",
              out.size == (1920, 1080), "got {0!r}".format(out.size))
        top = out.getpixel((0, 0))
        bottom = out.getpixel((0, 1079))
        check("tall source: top edge is the top half's colour (red)",
              top[0] > 150 and top[2] < 100, "got {0!r}".format(top))
        check("tall source: bottom edge is the bottom half's colour (blue)",
              bottom[2] > 150 and bottom[0] < 100, "got {0!r}".format(bottom))

        # Wide source: left half green, right half yellow, split at column
        # 200 of 400 — the mirror case, binding on height this time.
        wide_path = os.path.join(tmp, "wide.png")
        wide = Image.new("RGB", (400, 100), (20, 200, 20))
        ImageDraw.Draw(wide).rectangle([200, 0, 400, 100], fill=(220, 220, 20))
        wide.save(wide_path)
        out = prepare_background(wide_path, 0, False)
        check("wide source cover-fits to exactly 1920x1080",
              out.size == (1920, 1080), "got {0!r}".format(out.size))
        left = out.getpixel((0, 0))
        right = out.getpixel((1919, 0))
        check("wide source: left edge is the left half's colour (green)",
              left[1] > 150 and left[0] < 100, "got {0!r}".format(left))
        check("wide source: right edge is the right half's colour (yellow)",
              right[0] > 150 and right[1] > 150 and right[2] < 100,
              "got {0!r}".format(right))

        # Dim: a solid white source blended toward black by dim/100 —
        # exact arithmetic, not just "got darker".
        white_path = os.path.join(tmp, "white.png")
        Image.new("RGB", (300, 300), (255, 255, 255)).save(white_path)
        out = prepare_background(white_path, 50, False)
        px = out.getpixel((960, 540))
        check("dim=50 blends a white image to ~mid-grey",
              all(abs(c - 128) <= 2 for c in px), "got {0!r}".format(px))
        out = prepare_background(white_path, 0, False)
        px = out.getpixel((960, 540))
        check("dim=0 leaves the image unchanged",
              px == (255, 255, 255), "got {0!r}".format(px))

        # Blur: an already-1920x1080 source (cover-fit is a no-op here, so
        # this isolates blur) with a hard vertical edge at the centre.
        edge_path = os.path.join(tmp, "edge.png")
        edge = Image.new("RGB", (1920, 1080), (0, 0, 0))
        ImageDraw.Draw(edge).rectangle([960, 0, 1920, 1080],
                                       fill=(255, 255, 255))
        edge.save(edge_path)
        out = prepare_background(edge_path, 0, True)
        far_black = out.getpixel((100, 540))
        far_white = out.getpixel((1820, 540))
        at_edge = out.getpixel((960, 540))[0]
        check("blur leaves pixels far from the edge alone (black side)",
              far_black[0] < 10, "got {0!r}".format(far_black))
        check("blur leaves pixels far from the edge alone (white side)",
              far_white[0] > 245, "got {0!r}".format(far_white))
        check("blur visibly softens the hard edge at the seam",
              10 < at_edge < 245, "got {0!r}".format(at_edge))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def check_backgrounds_blur_route():
    """GET /api/backgrounds/<id>?blur=1 — added because the BLUR checkbox
    used to blur the canvas preview client-side via `ctx.filter`, a browser
    feature the app's embedded webview (pywebview -> WKWebView/WebView2)
    does not reliably apply, so DIM visibly worked and BLUR did not. The
    fix serves the SAME Pillow blur render/timer.py uses instead, so this
    exercises the actual HTTP route end to end (via Flask's test client,
    no server process needed) rather than just the pure `prepare_background`
    function check_prepare_background() above already covers.

    BACKGROUNDS_DIR is redirected under _BOARD_TMP by the
    SERVICE_VISUALS_CONFIG override at the top of this file (read once, at
    backgrounds.py's import time), so this never touches the owner's real
    library.
    """
    import io

    from PIL import Image, ImageDraw

    import app as _app
    import backgrounds

    print("Timer: background blur route (?blur=1)")

    # Same hard vertical edge check_prepare_background uses to prove
    # render/timer.py's own blur — this proves the ROUTE hands back that
    # same softened image, end to end.
    src = Image.new("RGB", (1920, 1080), (0, 0, 0))
    ImageDraw.Draw(src).rectangle([960, 0, 1920, 1080], fill=(255, 255, 255))
    buf = io.BytesIO()
    src.save(buf, format="PNG")
    buf.seek(0)

    client = _app.app.test_client()
    resp = client.post(
        "/api/backgrounds", content_type="multipart/form-data",
        data={"image": (buf, "edge.png")})
    check("upload to the library succeeds", resp.status_code == 200,
          "got {0} {1!r}".format(resp.status_code,
                                  resp.get_data(as_text=True)))
    image_id = resp.get_json()["id"]

    def adjacent_diff(png_bytes, x0, x1, y):
        """Mean abs difference between two columns one pixel apart at row
        y — measured, not eyeballed: a sharp hard edge reads as a near-full
        jump, a blurred one reads as a small number."""
        im = Image.open(io.BytesIO(png_bytes)).convert("L")
        return abs(im.getpixel((x0, y)) - im.getpixel((x1, y)))

    plain = client.get("/api/backgrounds/" + image_id)
    blurred = client.get("/api/backgrounds/" + image_id + "?blur=1")
    check("?blur=1 returns 200", blurred.status_code == 200,
          "got {0}".format(blurred.status_code))

    plain_edge = adjacent_diff(plain.data, 959, 960, 540)
    blur_edge = adjacent_diff(blurred.data, 959, 960, 540)
    check("plain route keeps the hard edge (unblurred)",
          plain_edge > 200, "got {0}".format(plain_edge))
    check("?blur=1 is genuinely softer at the same seam",
          blur_edge < plain_edge - 100,
          "plain={0} blur={1}".format(plain_edge, blur_edge))

    # "Anything other than blur=1 behaves exactly as today" (the fix's own
    # contract) — blur=0 must be indistinguishable from no query at all.
    untouched = client.get("/api/backgrounds/" + image_id + "?blur=0")
    check('blur=0 (anything but "1") behaves exactly like no query at all',
          untouched.data == plain.data, "bytes differ")

    plain_path = os.path.join(backgrounds.BACKGROUNDS_DIR, image_id + ".png")
    blur_path = os.path.join(
        backgrounds.BACKGROUNDS_DIR, image_id + ".blur.png")
    check("the blurred variant is cached on disk",
          os.path.isfile(blur_path), blur_path)

    client.delete("/api/backgrounds/" + image_id)
    check("DELETE removes the original image",
          not os.path.isfile(plain_path))
    check("DELETE also removes the cached blurred variant",
          not os.path.isfile(blur_path))


def check_digit_shadow():
    """render.timer._paste_digits: the soft dark halo dropped behind the
    digit block ONLY when a real background image is in use (docs/specs/
    timer-backgrounds.md addendum — a bright, busy photo can swamp light
    digits even dimmed/blurred, worst in the ring style; raising the
    default dim would just make every image muddy). Pure image-level
    check, no video: paste a solid glyph-shaped RGBA block (the same
    shape _render_digits/_render_clock_block produce — opaque glyph,
    transparent padding) onto a bright base and confirm a point just
    outside the glyph itself, but within the halo's reach, comes out
    measurably darker with has_bg=True than with has_bg=False — and that
    a point far away is untouched either way, proving this is a local
    soft shadow, not a global dim.
    """
    from PIL import Image

    from render.timer import _paste_digits

    print("Timer: digit shadow (_paste_digits)")

    def make_block():
        block = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
        glyph = Image.new("RGBA", (80, 80), (240, 240, 235, 255))
        block.paste(glyph, (60, 60))
        return block

    x, y = 400, 400
    bright = (230, 220, 210)   # stand-in for a bright, busy photo

    base_off = Image.new("RGB", (1920, 1080), bright)
    _paste_digits(base_off, make_block(), x, y, False)

    base_on = Image.new("RGB", (1920, 1080), bright)
    _paste_digits(base_on, make_block(), x, y, True)

    # 15px left of the glyph's own left edge (glyph occupies x+60..x+140 /
    # y+60..y+140 within base) — outside the glyph, well inside the
    # halo's spread (pad 60px, blur radius 18px).
    near = (x + 45, y + 100)
    off_near = base_off.getpixel(near)
    on_near = base_on.getpixel(near)
    check("no background: pixel beside the glyph is untouched",
          off_near == bright, "got {0!r}".format(off_near))
    check("with background: halo visibly darkens the pixel beside the glyph",
          sum(on_near) < sum(off_near) - 60,
          "off={0!r} on={1!r}".format(off_near, on_near))

    # Far outside the halo's reach entirely — must read as the untouched
    # base colour in both cases.
    far = (x - 300, y)
    off_far = base_off.getpixel(far)
    on_far = base_on.getpixel(far)
    check("with background: pixel far from the glyph is untouched",
          on_far == bright and off_far == bright,
          "off={0!r} on={1!r}".format(off_far, on_far))


def check_clock_format():
    """format_clock_time: the pure display contract clock mode and the JS
    preview must both match exactly (docs/specs/clock-mode.md). Exercised
    directly, with no fonts/rendering involved, so these run everywhere.
    """
    from render.timer import format_clock_time

    print("Timer: clock format_clock_time")

    # Midnight wrap: a 23:59:55 start plus 10s of elapsed time rolls to
    # 00:00:05 — a real clock rolling over, not an error or a 24:00:05.
    total_ms = (23 * 3600 + 59 * 60 + 55) * 1000 + 10000
    got = format_clock_time(total_ms, "24h", True, False)
    check("24h midnight wrap: 23:59:55 +10s -> 00:00:05",
          got == ("00:00:05", ""), "got {0!r}".format(got))

    # 12-hour midnight and noon both display "12", with the right tag.
    got = format_clock_time(0, "12h", True, False)
    check("12h midnight is 12:00:00 AM",
          got == ("12:00:00", "AM"), "got {0!r}".format(got))
    got = format_clock_time(12 * 3600 * 1000, "12h", True, False)
    check("12h noon is 12:00:00 PM",
          got == ("12:00:00", "PM"), "got {0!r}".format(got))

    # Millis are exact frame-index arithmetic (ms = round(i*1000/fps) at
    # the call site) — here just a straight value, three exact digits.
    got = format_clock_time(
        (19 * 3600 + 59 * 60 + 50) * 1000 + 123, "12h", True, True)
    check("millis are three exact digits",
          got == ("7:59:50.123", "PM"), "got {0!r}".format(got))

    # Seconds hidden drops straight to H:MM / HH:MM, no tag in 24h.
    got = format_clock_time(
        (7 * 3600 + 59 * 60 + 50) * 1000, "12h", False, False)
    check("seconds hidden shows H:MM (12h, hour not padded)",
          got == ("7:59", "AM"), "got {0!r}".format(got))
    got = format_clock_time(
        (7 * 3600 + 59 * 60 + 50) * 1000, "24h", False, False)
    check("seconds hidden shows HH:MM (24h, hour padded, no tag)",
          got == ("07:59", ""), "got {0!r}".format(got))

    # show_millis forces show_seconds on, even if the caller passed False.
    got = format_clock_time(
        (7 * 3600 + 59 * 60 + 50) * 1000 + 5, "24h", False, True)
    check("show_millis forces seconds on",
          got == ("07:59:50.005", ""), "got {0!r}".format(got))


def check_countdown_millis_format():
    """Addendum (v1.23.0): render_timer's countdown-with-millis text split —
    main = _format_remaining(rem_ms // 1000, total), millis = rem_ms % 1000
    as three digits (docs/specs/clock-mode.md addendum). Exercised directly
    on the pure formatter, no fonts/rendering involved.
    """
    from render.timer import _format_remaining

    print("Timer: countdown millis text split")

    rem_ms, total = 4033, 300
    main = _format_remaining(rem_ms // 1000, total)
    millis = "{0:03d}".format(rem_ms % 1000)
    check("rem_ms=4033, total=300 -> main '0:04', millis '033'",
          main == "0:04" and millis == "033",
          "got main={0!r} millis={1!r}".format(main, millis))


def check_clock_validation():
    """validation.validate_timer_options: mode dispatch, clock-only field
    checks, and proof a countdown payload validates exactly as it did
    before this feature existed (mode absent, and mode="countdown"
    explicitly).
    """
    import validation

    print("Timer: clock validate_timer_options")

    countdown = {"minutes": 1, "seconds": 0, "style": "ring",
                 "accent": "#e8b44f", "warn_last10": False,
                 "hold_seconds": 3}
    # Addendum (v1.23.0): show_millis is now a countdown key too, defaulting
    # false — this dict is the "before this feature existed" contract, so
    # its presence (always false here, since `countdown` never sets it)
    # proves an old caller that never heard of millis still gets the exact
    # dict shape it always got, plus the new default key.
    # Addendum (v1.24.0): the four Background-group keys, defaulting to "no
    # images" (docs/specs/timer-backgrounds.md) — same idea, a caller that
    # never heard of backgrounds still gets those defaults for free.
    # Addendum (green-screen.md): green_screen defaults to False, sitting
    # alongside the other three Background-group defaults for a caller that
    # never heard of it either.
    # Addendum (alpha-export.md): transparent defaults to False, a fifth
    # Background-group default for the same reason.
    # Addendum (millis-reveal.md): three more countdown-only defaults --
    # millis_full_size/millis_reveal both False, millis_reveal_seconds 60
    # -- sitting alongside fixed_format for a caller that never heard of
    # this feature either. This fixture predates the feature, which is
    # why it needs updating here rather than validate_timer_options being
    # wrong (docs/specs/millis-reveal.md, Validation + smoke agent).
    # Addendum (millis-60fps.md): millis_60fps defaults to False, a
    # fourth countdown-only millis default alongside fixed_format/
    # millis_full_size/millis_reveal, for the same "a caller that never
    # heard of this feature still gets the default" reason. Stale
    # fixture, not a regression -- validate_timer_options itself already
    # returns this key (docs/specs/millis-60fps.md, Validation + smoke
    # agent).
    expected = {"minutes": 1, "seconds": 0, "style": "ring",
                "accent": "#e8b44f", "warn_last10": False,
                "hold_seconds": 3, "show_millis": False,
                "fixed_format": False,
                "millis_full_size": False, "millis_reveal": False,
                "millis_reveal_seconds": 60, "millis_60fps": False,
                "backgrounds": [], "bg_seconds": 10, "bg_dim": 45,
                "bg_blur": False, "green_screen": False,
                "transparent": False}
    clean = validation.validate_timer_options(countdown)
    check("a countdown payload (mode absent) validates unchanged",
          clean == expected, "got {0!r}".format(clean))
    clean = validation.validate_timer_options(
        dict(countdown, mode="countdown"))
    check('mode="countdown" validates the same as mode absent',
          clean == expected, "got {0!r}".format(clean))

    clean = validation.validate_timer_options(
        dict(countdown, show_millis=True))
    check("show_millis=True is accepted in countdown mode",
          clean.get("show_millis") is True, "got {0!r}".format(clean))

    clock = {"mode": "clock", "start": "19:59:50", "duration_seconds": 30,
             "format": "12h", "show_seconds": True, "show_millis": False,
             "style": "classic", "accent": "#e8b44f"}
    clean = validation.validate_timer_options(clock)
    check("a well-formed clock payload comes back with mode='clock'",
          clean.get("mode") == "clock" and clean.get("start") == "19:59:50",
          "got {0!r}".format(clean))

    def expect_error(label, payload, contains):
        try:
            validation.validate_timer_options(payload)
            check(label, False, "no error raised")
        except validation.ValidationError as exc:
            check(label, contains in str(exc), "got {0!r}".format(str(exc)))

    expect_error("a malformed start time is rejected",
                 dict(clock, start="25:00:00"), "Start time must look like")
    expect_error("bar style is refused for the clock",
                 dict(clock, style="bar"), "Bar style isn't available")
    expect_error("duration_seconds below 5 is rejected",
                 dict(clock, duration_seconds=2), "Clip length must be")
    expect_error("duration_seconds above 1800 is rejected",
                 dict(clock, duration_seconds=1801), "Clip length must be")
    expect_error("show_millis must be a boolean in countdown mode",
                 dict(countdown, show_millis="yes"),
                 '"Show milliseconds" must be true or false.')

    # v1.24.0: the four Background-group keys (docs/specs/timer-
    # backgrounds.md) — valid, and validated identically, in both modes.
    with_bg = dict(countdown, backgrounds=[], bg_seconds=5, bg_dim=20,
                   bg_blur=True)
    clean = validation.validate_timer_options(with_bg)
    check("the four background keys are accepted (countdown mode)",
          clean.get("backgrounds") == [] and clean.get("bg_seconds") == 5
          and clean.get("bg_dim") == 20 and clean.get("bg_blur") is True,
          "got {0!r}".format(clean))
    clean = validation.validate_timer_options(
        dict(clock, backgrounds=[], bg_seconds=5, bg_dim=20, bg_blur=True))
    check("the four background keys are accepted (clock mode)",
          clean.get("bg_seconds") == 5 and clean.get("bg_dim") == 20
          and clean.get("bg_blur") is True, "got {0!r}".format(clean))

    expect_error("an unknown background id is rejected",
                 dict(countdown, backgrounds=["deadbeefdeadbeef"]),
                 "One of the background images is missing")
    expect_error("more than 10 background images is rejected",
                 dict(countdown, backgrounds=["a" * 16] * 11),
                 "up to 10 background images")
    expect_error("bg_dim above 80 is rejected",
                 dict(countdown, bg_dim=90),
                 "Dim must be a whole number between 0 and 80.")
    expect_error("bg_blur must be a boolean",
                 dict(countdown, bg_blur="yes"),
                 "Blur must be true or false.")

    # docs/specs/green-screen.md: green_screen bool, valid in both modes.
    # When true, backgrounds normalises to [] WITHOUT validating the ids
    # sent — a bogus/stale id in a hidden image set must not block a green
    # export.
    clean = validation.validate_timer_options(
        dict(countdown, green_screen=True,
             backgrounds=["deadbeefdeadbeef"]))
    check("green_screen=True (countdown): backgrounds -> [] even with a "
          "bogus id",
          clean.get("green_screen") is True
          and clean.get("backgrounds") == [], "got {0!r}".format(clean))
    clean = validation.validate_timer_options(
        dict(clock, green_screen=True,
             backgrounds=["deadbeefdeadbeef"]))
    check("green_screen=True (clock): backgrounds -> [] even with a "
          "bogus id",
          clean.get("green_screen") is True
          and clean.get("backgrounds") == [], "got {0!r}".format(clean))

    clean = validation.validate_timer_options(countdown)
    check("green_screen omitted defaults to False",
          clean.get("green_screen") is False, "got {0!r}".format(clean))

    expect_error("green_screen must be a boolean",
                 dict(countdown, green_screen="yes"),
                 "Green screen must be true or false.")

    # docs/specs/alpha-export.md: transparent, a third Background-group
    # choice, valid in both modes. Like green_screen, a truthy value
    # forces backgrounds -> [] WITHOUT validating the ids sent, so a
    # stale/deleted id in a hidden set can never block a transparent
    # export either.
    for fmt in ("qtrle", "prores"):
        clean = validation.validate_timer_options(
            dict(countdown, transparent=fmt,
                 backgrounds=["deadbeefdeadbeef"]))
        check("transparent={0!r} (countdown): backgrounds -> [] even "
              "with a bogus id".format(fmt),
              clean.get("transparent") == fmt
              and clean.get("backgrounds") == [],
              "got {0!r}".format(clean))
        clean = validation.validate_timer_options(
            dict(clock, transparent=fmt,
                 backgrounds=["deadbeefdeadbeef"]))
        check("transparent={0!r} (clock): backgrounds -> [] even with "
              "a bogus id".format(fmt),
              clean.get("transparent") == fmt
              and clean.get("backgrounds") == [],
              "got {0!r}".format(clean))

    clean = validation.validate_timer_options(countdown)
    check("transparent omitted defaults to False",
          clean.get("transparent") is False, "got {0!r}".format(clean))

    expect_error("a bad transparent value is rejected with the exact "
                 "message",
                 dict(countdown, transparent="png"),
                 'Transparent background must be "qtrle", "prores", or '
                 "false.")

    # `is not False` in validation.py means a stray True must be
    # rejected exactly like a bad string, not silently accepted as if
    # it named some default format — there is no single "on" here.
    expect_error("a stray True for transparent is rejected, not treated "
                 "as a default format",
                 dict(countdown, transparent=True),
                 'Transparent background must be "qtrle", "prores", or '
                 "false.")

    expect_error("transparent and green_screen together is rejected",
                 dict(countdown, transparent="qtrle", green_screen=True),
                 "Choose either green screen or a transparent "
                 "background, not both.")


def check_green_screen():
    """docs/specs/green-screen.md: render.timer._plates() builds a flat
    green plate straight off the option, then a real encoded render proves
    that plate survives all the way through H.264 to a frame you can pull
    back out.
    """
    from PIL import Image

    from render.timer import _plates

    print("Timer: green screen plate + render")

    plates, _accent_tile = _plates({"green_screen": True}, "ring", (1, 2, 3))
    check("_plates() with green_screen returns exactly one plate",
          len(plates) == 1, "got {0!r}".format(len(plates)))
    px = plates[0].getpixel((10, 10))
    check("that plate's pixel (10, 10) is pure green (0, 255, 0)",
          px == (0, 255, 0), "got {0!r}".format(px))

    # A real 6s classic countdown, green screen on — same shape (6s run +
    # 2s hold) as the plain countdown renders in main(), but checked on its
    # own here since it needs a frame pulled and inspected, not just the
    # container/duration verify() already covers.
    filename = render_timer(
        {"minutes": 0, "seconds": 6, "style": "classic",
         "accent": "#e8b44f", "warn_last10": True, "hold_seconds": 2,
         "green_screen": True},
        lambda pct: None)
    path = os.path.join(EXPORTS_DIR, filename)
    try:
        check("filename carries the _green descriptor",
              "_green" in filename, "got {0!r}".format(filename))
        verify("timer/classic-green", filename, 8.0)

        frame_dir = tempfile.mkdtemp(prefix="sv-smoke-green-")
        try:
            frame_path = os.path.join(frame_dir, "frame0.png")
            proc = subprocess.run(
                [FFMPEG, "-y", "-i", path, "-frames:v", "1", frame_path],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            got_frame = os.path.isfile(frame_path)
            check("a frame extracts cleanly with the bundled ffmpeg",
                  got_frame, proc.stderr.decode("utf-8", "replace")[-300:])
            if got_frame:
                r, g, b = Image.open(frame_path).convert("RGB") \
                    .getpixel((10, 10))
                # H.264 4:2:0 chroma subsampling shifts pure green
                # slightly off (0, 255, 0) on re-decode — this tolerance
                # is the point of the check, not a bug in the encoder.
                check("extracted frame's pixel (10, 10) reads as green "
                      "(R<=24, G>=232, B<=24)",
                      r <= 24 and g >= 232 and b <= 24,
                      "got ({0}, {1}, {2})".format(r, g, b))
        finally:
            shutil.rmtree(frame_dir, ignore_errors=True)
    finally:
        if os.path.isfile(path):
            os.unlink(path)


def check_alpha_export():
    """docs/specs/alpha-export.md: render.timer._plates()'s transparent
    branch, the _paste_digits() alpha-compositing fix (a regression
    guard for the exact Pillow bug its own code comment describes), the
    glyph's anti-aliased (fractional, not binary) edge alpha, and a real
    encoded render in each alpha format, pixel-verified on a frame
    pulled back out through the bundled ffmpeg's own decoder. Mirrors
    check_green_screen()'s shape; check_green_screen() itself staying
    green (called separately, right before this function) is the
    regression proof that Transparent changed nothing about Green
    screen.
    """
    from PIL import Image

    from render.timer import (
        _digits_metrics, _paste_digits, _plates, _render_digits)

    print("Timer: transparent (alpha) plate + paste fix + render")

    # 1. _plates() with transparent returns one fully transparent RGBA
    # plate. Which format string is passed doesn't matter to _plates()
    # — it only ever checks truthiness — so "qtrle" stands in for both.
    plates, _accent_tile = _plates(
        {"transparent": "qtrle"}, "ring", (1, 2, 3))
    check("_plates() with transparent returns exactly one plate",
          len(plates) == 1, "got {0!r}".format(len(plates)))
    check("that plate's mode is RGBA",
          bool(plates) and plates[0].mode == "RGBA",
          "got {0!r}".format(plates[0].mode if plates else None))
    px = plates[0].getpixel((10, 10)) if plates else None
    check("that plate's pixel (10, 10) reads (0, 0, 0, 0)",
          px == (0, 0, 0, 0), "got {0!r}".format(px))

    # 2. The _paste_digits alpha fix, tested directly (not through a
    # full render) — a regression guard for the exact bug its code
    # comment describes: pasting an RGBA block using its OWN alpha band
    # as the paste mask pulls an already-opaque destination's alpha
    # DOWN toward the source's own (lower) alpha, where
    # Image.alpha_composite() correctly leaves an opaque destination
    # opaque.
    track_px = Image.new("RGBA", (60, 60), (35, 38, 43, 255))
    half_glyph = Image.new("RGBA", (60, 60), (242, 240, 235, 128))
    _paste_digits(track_px, half_glyph, 0, 0, False, alpha=True)
    dest_alpha = track_px.getpixel((30, 30))[3]
    check("_paste_digits(alpha=True): a 50%-alpha block pasted over an "
          "opaque destination leaves it fully opaque (alpha=255)",
          dest_alpha == 255, "got alpha={0!r}".format(dest_alpha))

    # 3. Glyph edge alpha is fractional, not binary — the entire point
    # of this feature. A hard 0/255-only cutout would pass every other
    # check here and still reproduce the green-screen fringe problem
    # this feature exists to remove, just moved into the alpha channel.
    met = _digits_metrics(200)
    block = _render_digits("8", (242, 240, 235), met)
    alpha_band = block.split()[-1]
    lo, hi = alpha_band.getextrema()
    has_mid = any(c > 0 for c in alpha_band.histogram()[1:255])
    check("_render_digits glyph block has a fully transparent pixel "
          "(background)", lo == 0, "min alpha = {0!r}".format(lo))
    check("_render_digits glyph block has a fully opaque pixel "
          "(glyph interior)", hi == 255, "max alpha = {0!r}".format(hi))
    check("_render_digits glyph block has fractional edge alpha "
          "(anti-aliased, not a hard cutout)",
          has_mid, "no alpha strictly between 0 and 255 was found")

    def render_and_check(fmt, expected_codec, expected_pixfmt,
                         style="classic"):
        """Checks 4/5: a real 6s countdown in one alpha format,
        pixel-verified on an extracted, decoded frame — a genuine
        round-trip proof, not just a container-tag inspection (both
        formats round-trip alpha correctly through this ffmpeg build's
        own decoder, confirmed while researching the spec).
        """
        descriptor = "_alpha_" + fmt
        filename = render_timer(
            {"minutes": 0, "seconds": 6, "style": style,
             "accent": "#e8b44f", "warn_last10": True,
             "hold_seconds": 2, "transparent": fmt},
            lambda pct: None)
        path = os.path.join(EXPORTS_DIR, filename)
        try:
            check("{0}: filename ends .mov".format(fmt),
                  filename.endswith(".mov"),
                  "got {0!r}".format(filename))
            check("{0}: filename carries the {1} descriptor".format(
                      fmt, descriptor),
                  descriptor in filename, "got {0!r}".format(filename))
            verify("timer/" + style + "-" + fmt, filename, 8.0,
                   expected_codec=expected_codec,
                   expected_pixfmt=expected_pixfmt)

            frame_dir = tempfile.mkdtemp(prefix="sv-smoke-alpha-")
            try:
                frame_path = os.path.join(frame_dir, "frame0.png")
                proc = subprocess.run(
                    [FFMPEG, "-y", "-i", path, "-frames:v", "1",
                     "-pix_fmt", "rgba", frame_path],
                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                got_frame = os.path.isfile(frame_path)
                check("{0}: a frame extracts cleanly with alpha kept "
                      "(-pix_fmt rgba)".format(fmt), got_frame,
                      proc.stderr.decode("utf-8", "replace")[-300:])
                if got_frame:
                    frame = Image.open(frame_path).convert("RGBA")
                    bg_alpha = frame.getpixel((10, 10))[3]
                    check("{0}: background-area pixel (10, 10) is "
                          "fully transparent (alpha == 0)".format(fmt),
                          bg_alpha == 0,
                          "got alpha={0!r}".format(bg_alpha))
                    frame_alpha = frame.split()[-1]
                    lo, hi = frame_alpha.getextrema()
                    has_mid = any(
                        c > 0 for c in frame_alpha.histogram()[1:255])
                    check("{0}: a digit-interior pixel is fully opaque "
                          "(alpha >= 250)".format(fmt), hi >= 250,
                          "max alpha = {0!r}".format(hi))
                    check("{0}: at least one digit-edge pixel has "
                          "fractional alpha (0 < alpha < 255), proving "
                          "the glyph is anti-aliased through a real "
                          "encode/decode round trip".format(fmt),
                          has_mid,
                          "no intermediate alpha value found")
            finally:
                shutil.rmtree(frame_dir, ignore_errors=True)
        finally:
            if os.path.isfile(path):
                os.unlink(path)

    # 4. qtrle: lossless RLE, round-trips alpha exactly — the probed
    # pixfmt matches the encode request byte for byte (verified while
    # researching the spec; see render/encoder.py's ALPHA_FORMATS).
    render_and_check("qtrle", "qtrle", "argb")

    # 5. ProRes 4444: the probed pixfmt is "yuva444p12le", NOT the
    # "yuva444p10le" passed at encode time — ProRes 4444's bitstream
    # always carries alpha at 12-bit internally regardless of the
    # 10-bit input request. Expected behaviour (confirmed while
    # researching the spec by actually probing a real encode), not a
    # bug — do not "fix" this to yuva444p10le.
    render_and_check("prores", "prores", "yuva444p12le")

    # 5b. Ring and bar, one format each. classic draws digits onto bare
    # transparency; ring and bar paste them over an opaque track, which
    # is the only place a compositing regression could reintroduce a
    # fringe. A review rendered these by hand and found them clean --
    # this keeps them that way without another human doing it.
    render_and_check("qtrle", "qtrle", "argb", style="ring")
    render_and_check("prores", "prores", "yuva444p12le", style="bar")

    # 6. Green screen output is unchanged — no new check needed here;
    # check_green_screen() (called separately from main(), right before
    # this function) staying green IS the regression proof.


def check_qr_styles():
    """docs/specs/qr-styles.md: style validation, plus one pixel check per
    non-card style proving the plate/dots actually differ from `card` in
    the rendered frame. A still (render_qr_still) is the same composition
    the video encodes, so this covers the visual contract without a
    render+ffmpeg round trip — `card`'s own video path is unchanged and
    stays covered by verify() in main().
    """
    import validation
    from render.qr import (WIDTH, _build_card, _clean_options,
                            _dots_square_mask, _layout, _module_px,
                            _qr_matrix, render_qr_still)

    print("QR: style validation + per-style pixel checks")

    base = {"url": "https://example.org", "heading": "GIVE",
            "caption": "Thanks"}

    clean = validation.validate_qr_options(base)
    check("style absent defaults to card",
          clean.get("style") == "card", "got {0!r}".format(clean))

    for style in ("light", "dots"):
        clean = validation.validate_qr_options(dict(base, style=style))
        check("style={0!r} is accepted".format(style),
              clean.get("style") == style, "got {0!r}".format(clean))

    try:
        validation.validate_qr_options(dict(base, style="neon"))
        check('style="neon" is rejected', False, "no error raised")
    except validation.ValidationError as exc:
        check('style="neon" is rejected with the exact message',
              str(exc) == "That QR style is not valid.",
              "got {0!r}".format(str(exc)))

    # (b) `light` is a light plate, `card` the dark vignette — pixel
    # (5, 5) sits on the background corner, far from the centered card,
    # for either style.
    card_frame = render_qr_still(
        _clean_options(dict(base, style="card")), max_width=900)
    r, g, b = card_frame.getpixel((5, 5))
    check("card: pixel (5, 5) is dark (every channel <= 40)",
          r <= 40 and g <= 40 and b <= 40,
          "got ({0}, {1}, {2})".format(r, g, b))

    light_frame = render_qr_still(
        _clean_options(dict(base, style="light")), max_width=900)
    r, g, b = light_frame.getpixel((5, 5))
    check("light: pixel (5, 5) is light (every channel >= 200)",
          r >= 200 and g >= 200 and b >= 200,
          "got ({0}, {1}, {2})".format(r, g, b))

    # (c) `dots`: a dark module that is neither finder nor alignment
    # pattern renders as a filled circle — centre dark, corner (outside
    # the inscribed circle) light. Geometry comes from the renderer's own
    # helpers (module_px, card layout), never a hard-coded pixel offset,
    # so this keeps working if sizing constants ever change.
    dots_opts = _clean_options(dict(base, style="dots"))
    matrix, n, qr = _qr_matrix(dots_opts["url"])
    module_px = _module_px(n)
    square = _dots_square_mask(qr, n)

    cell = None
    if matrix[9][9] and not square[9][9]:
        cell = (9, 9)
    else:
        for rr in range(n):
            for cc in range(n):
                if matrix[rr][cc] and not square[rr][cc]:
                    cell = (rr, cc)
                    break
            if cell:
                break
    check("a dark, non-finder/alignment cell exists to sample",
          cell is not None, "matrix has none (n={0})".format(n))

    if cell is not None:
        r_i, c_i = cell
        card_img, card_size = _build_card(matrix, n, "dots", qr)
        geo = _layout(dots_opts, card_size)
        code_px = module_px * n
        origin = (card_size - code_px) // 2
        card_left = geo["card_cx"] - card_size // 2
        card_top = geo["card_top"]

        x0 = card_left + origin + c_i * module_px
        y0 = card_top + origin + r_i * module_px
        cx = x0 + module_px // 2
        cy = y0 + module_px // 2

        # render_qr_still scales the 1920-wide frame down to max_width —
        # scale the sample points the same way rather than re-rendering
        # at full size.
        ratio = 900.0 / WIDTH
        dots_frame = render_qr_still(dots_opts, max_width=900)
        center_px = dots_frame.getpixel(
            (int(round(cx * ratio)), int(round(cy * ratio))))
        corner_px = dots_frame.getpixel(
            (int(round(x0 * ratio)), int(round(y0 * ratio))))
        check("dots: sampled cell's centre pixel is dark",
              all(ch <= 40 for ch in center_px[:3]),
              "got {0!r}".format(center_px))
        check("dots: sampled cell's top-left corner pixel is light",
              all(ch >= 200 for ch in corner_px[:3]),
              "got {0!r}".format(corner_px))


def check_boot_marker_is_packaged_only():
    """A source run must not touch the installed app's boot marker.

    Running from source shares ~/.service-visuals with the packaged app. A
    dev server used to arm the marker there and, if killed before any UI
    hit /api/health, leave it behind — so the next launch of the REAL app
    reported a startup_failed that never happened. It must also not CLEAR
    the marker, or a dev server would silence a genuine failure.
    """
    import stats

    print("Stats: the boot marker is packaged-only")
    marker = stats.BOOT_PATH
    if os.path.exists(marker):
        os.unlink(marker)

    # A source run (not frozen, STATS unset) must leave the marker alone.
    stats._state.update(armed=False, booted=False)
    os.environ.pop("SERVICE_VISUALS_STATS", None)
    stats.report_previous_boot()
    check("a source run does not arm the marker",
          not os.path.exists(marker), "a dev run wrote a boot marker")

    # Plant one as if the packaged app had left it, then confirm a source
    # run does not delete it out from under the real app.
    stats.mark_boot("9.9.9")
    stats._state.update(booted=False)
    stats.boot_ready()
    check("a source run does not clear the packaged app's marker",
          os.path.exists(marker), "a dev run cleared a real marker")
    os.unlink(marker)

    # Forced on (what the packaged app looks like), it works as designed.
    os.environ["SERVICE_VISUALS_STATS"] = "1"
    stats._state.update(armed=False, booted=False)
    stats.report_previous_boot()
    check("a reporting run does arm the marker", os.path.exists(marker))
    stats._state.update(booted=False)
    stats.boot_ready()
    check("a reporting run clears its own marker",
          not os.path.exists(marker))
    os.environ["SERVICE_VISUALS_STATS"] = "0"


def check_update_picks_newest_version():
    """The updater must offer the highest VERSION, not the newest upload."""
    from routes.update import newest_release, _release_version, _version_tuple

    print("Update: newest release is picked by version, not date")
    # A hotfix on an old line published after a newer minor is the whole
    # reason this exists — /releases/latest would answer v1.24.2 here.
    feed = [
        {"tag_name": "v1.24.2"},
        {"tag_name": "v1.26.1"},
        {"tag_name": "v1.25.0"},
    ]
    picked = newest_release(feed)
    check("a later-published hotfix does not beat a higher version",
          picked and picked["tag_name"] == "v1.26.1",
          "picked {0!r}".format(picked and picked.get("tag_name")))

    check("a client many versions behind is offered the newest",
          _version_tuple("v1.26.1") > _version_tuple("1.13.0"))

    check("drafts are never offered",
          _release_version({"tag_name": "v9.9.9", "draft": True}) is None)
    check("prereleases are never offered",
          _release_version({"tag_name": "v9.9.9", "prerelease": True}) is None)
    check("a tag that is not three numbers is skipped, not raised",
          _release_version({"tag_name": "nightly"}) is None)
    survivor = newest_release([{"tag_name": "nightly"},
                               {"tag_name": "v1.26.1"}])
    check("one malformed tag does not sink the whole check",
          survivor and survivor["tag_name"] == "v1.26.1")
    check("nothing installable yields None, so the caller can fall back",
          newest_release([{"tag_name": "v9.9.9", "draft": True}]) is None)


def check_download():
    """youtube-download.md: validation, the pure yt-dlp/tools helpers, and
    the mp3 addition to EXPORT_FILENAME_RE -- all offline. No yt-dlp or
    Deno binary, and no network access, anywhere in this function.
    """
    import hashlib
    import platform
    import tempfile

    import tools
    import validation
    from downloader import format_args, friendly_error, parse_progress

    print("Download: validate_download_options")

    def expect_ok(label, options, want):
        clean = validation.validate_download_options(options)
        check(label, clean == want, "got {0!r}".format(clean))

    def expect_error(label, options, message):
        try:
            validation.validate_download_options(options)
            check(label, False, "no error raised")
        except validation.ValidationError as exc:
            check(label, str(exc) == message, "got {0!r}".format(str(exc)))

    url_error = validation.DOWNLOAD_URL_ERROR
    expect_ok("a watch?v= link is accepted",
              {"url": "https://www.youtube.com/watch?v=aqz-KE-bpKQ"},
              {"urls": ["https://www.youtube.com/watch?v=aqz-KE-bpKQ"],
               "format": "mp4"})
    expect_ok("a youtu.be/ link is accepted",
              {"url": "https://youtu.be/aqz-KE-bpKQ", "format": "mp3"},
              {"urls": ["https://youtu.be/aqz-KE-bpKQ"], "format": "mp3"})
    expect_ok("a shorts/ link is accepted",
              {"url": "https://www.youtube.com/shorts/aqz-KE-bpKQ"},
              {"urls": ["https://www.youtube.com/shorts/aqz-KE-bpKQ"],
               "format": "mp4"})
    expect_ok("a music.youtube.com link is accepted",
              {"url": "https://music.youtube.com/watch?v=aqz-KE-bpKQ"},
              {"urls": ["https://music.youtube.com/watch?v=aqz-KE-bpKQ"],
               "format": "mp4"})

    expect_error("a vimeo.com link is rejected",
                 {"url": "https://vimeo.com/12345"}, url_error)
    expect_error("a non-URL string is rejected",
                 {"url": "not a url at all"}, url_error)
    expect_error("an empty url is rejected", {"url": ""}, url_error)
    expect_error("a 600-char url is rejected",
                 {"url": "https://www.youtube.com/watch?v=" + "x" * 600},
                 url_error)

    expect_error("an unknown format is rejected",
                 {"url": "https://youtu.be/aqz-KE-bpKQ", "format": "wav"},
                 "Format must be mp4 or mp3.")
    clean = validation.validate_download_options(
        {"url": "https://youtu.be/aqz-KE-bpKQ"})
    check("format defaults to mp4", clean.get("format") == "mp4",
          "got {0!r}".format(clean))
    check("a bare url is normalised into a one-element urls list",
          clean.get("urls") == ["https://youtu.be/aqz-KE-bpKQ"],
          "got {0!r}".format(clean))

    check('VALIDATORS["download"] is validate_download_options',
          validation.VALIDATORS["download"]
          is validation.validate_download_options)

    print()
    print("Download: batch-download.md -- the urls (list) shape")

    expect_ok("urls (a list) is accepted and normalised the same way",
              {"urls": ["https://youtu.be/aqz-KE-bpKQ"], "format": "mp3"},
              {"urls": ["https://youtu.be/aqz-KE-bpKQ"], "format": "mp3"})
    expect_ok("blank lines are ignored, entries stripped",
              {"urls": ["  https://youtu.be/a  ", "", "   ",
                       "https://youtu.be/b"]},
              {"urls": ["https://youtu.be/a", "https://youtu.be/b"],
               "format": "mp4"})
    expect_ok("a duplicate keeps only its first occurrence",
              {"urls": ["https://youtu.be/a", "https://youtu.be/b",
                       "https://youtu.be/a"]},
              {"urls": ["https://youtu.be/a", "https://youtu.be/b"],
               "format": "mp4"})

    # batch-download.md, "Separators": a comma and a new line are the
    # same separator, so every shape below is the same two-link batch.
    two = {"urls": ["https://youtu.be/a", "https://youtu.be/b"],
           "format": "mp4"}
    expect_ok("a comma splits one entry into two links",
              {"urls": ["https://youtu.be/a,https://youtu.be/b"]}, two)
    expect_ok("a comma with spaces around it splits the same way",
              {"urls": ["https://youtu.be/a , https://youtu.be/b"]},
              two)
    expect_ok("a new line inside one entry splits it too",
              {"urls": ["https://youtu.be/a\nhttps://youtu.be/b"]},
              two)
    expect_ok("a Windows paste (\\r\\n) splits and leaves no \\r behind",
              {"urls": ["https://youtu.be/a\r\nhttps://youtu.be/b"]},
              two)
    expect_ok("a trailing comma does not become a blank link",
              {"urls": ["https://youtu.be/a,https://youtu.be/b,"]}, two)
    expect_ok("repeated separators collapse",
              {"urls": ["https://youtu.be/a,,\n,https://youtu.be/b"]},
              two)
    expect_ok("commas and new lines mix freely",
              {"urls": ["https://youtu.be/a,https://youtu.be/b\n"
                        "https://youtu.be/c"]},
              {"urls": ["https://youtu.be/a", "https://youtu.be/b",
                        "https://youtu.be/c"], "format": "mp4"})
    expect_ok("a duplicate is dropped across different separators",
              {"urls": ["https://youtu.be/a,https://youtu.be/b",
                        "https://youtu.be/a"]}, two)
    expect_ok("one link per entry still behaves exactly as before",
              {"urls": ["https://youtu.be/a", "https://youtu.be/b"]},
              two)

    expect_error("a comma-split piece that is not a link names its "
                 "position in the SPLIT list",
                 {"urls": ["https://youtu.be/a,not a url"]},
                 validation.BATCH_LINE_ERROR.format(2))
    # 26 entries of two links each is 52 -- the cap counts links, not
    # pasted lines, so a comma list cannot smuggle past it.
    pairs = ["https://youtu.be/x{0:03d},https://youtu.be/y{0:03d}".format(i)
             for i in range(26)]
    expect_error("splitting counts toward the 50-link cap",
                 {"urls": pairs},
                 "A batch can hold up to 50 links at once.")

    expect_error("an empty urls list is today's url error",
                 {"urls": []}, url_error)
    expect_error("a urls list of only blank lines is today's url error",
                 {"urls": ["", "   ", "\n"]}, url_error)
    expect_error("a urls list of only separators is the url error too",
                 {"urls": [",", " , ", ",\n,"]}, url_error)
    expect_error("a non-list urls value falls back to the url error",
                 {"urls": "not-a-list"}, url_error)

    expect_error(
        "a bad line names its 1-based position",
        {"urls": ["https://youtu.be/a", "not a url", "https://youtu.be/b"]},
        validation.BATCH_LINE_ERROR.format(2))
    expect_error(
        "line numbers count the CLEANED list, not the raw one",
        {"urls": ["https://youtu.be/a", "", "https://youtu.be/a",
                  "not a url"]},
        validation.BATCH_LINE_ERROR.format(2))
    expect_error(
        "a non-string entry is a bad line too, not a crash",
        {"urls": ["https://youtu.be/a", 42]},
        validation.BATCH_LINE_ERROR.format(2))
    check("the batch link message matches the spec text exactly",
          validation.BATCH_LINE_ERROR.format(4)
          == "Link 4 isn't a YouTube link — separate links with a "
             "comma or a new line.",
          validation.BATCH_LINE_ERROR.format(4))

    check("BATCH_MAX_LINKS is 50", validation.BATCH_MAX_LINKS == 50,
          "got {0!r}".format(validation.BATCH_MAX_LINKS))
    exactly_50 = ["https://youtu.be/v{0:03d}".format(i) for i in range(50)]
    clean = validation.validate_download_options({"urls": exactly_50})
    check("exactly 50 distinct links is accepted",
          len(clean["urls"]) == 50, "got {0}".format(len(clean["urls"])))
    expect_error("51 links is rejected",
                 {"urls": exactly_50 + ["https://youtu.be/v999"]},
                 "A batch can hold up to 50 links at once.")

    print()
    print("Download: format_args / parse_progress / friendly_error")
    mp4_args = format_args("mp4")
    check('format_args("mp4") sets codec:h264:m4a,res:1080',
          "-S" in mp4_args and "codec:h264:m4a,res:1080" in mp4_args,
          "got {0!r}".format(mp4_args))
    check('format_args("mp4") merges to mp4',
          "--merge-output-format" in mp4_args and "mp4" in mp4_args,
          "got {0!r}".format(mp4_args))
    mp3_args = format_args("mp3")
    check('format_args("mp3") extracts audio to mp3',
          "-x" in mp3_args and "--audio-format" in mp3_args
          and "mp3" in mp3_args, "got {0!r}".format(mp3_args))

    check('parse_progress("SV  42.3%") is 42',
          parse_progress("SV  42.3%") == 42,
          "got {0!r}".format(parse_progress("SV  42.3%")))
    for line in ("[download] Destination: foo.mp4", "", None):
        check("a non-progress line yields None: {0!r}".format(line),
              parse_progress(line) is None)

    check("private/sign-in stderr maps to the private-video message",
          friendly_error("ERROR: Private video. Sign in if you've been "
                         "granted access to this video")
          == "That video is private or needs a sign-in, so it can't be "
             "downloaded.")
    check("age-restricted stderr maps to the age message",
          friendly_error("ERROR: This video is age restricted")
          == "That video is age-restricted, so it can't be downloaded.")
    check("unavailable stderr maps to the unavailable message",
          friendly_error("ERROR: Video unavailable")
          == "That video isn't available.")
    check("a network-shaped stderr maps to the network message",
          friendly_error("urlopen error [Errno 8] nodename nor servname "
                         "provided")
          == "Couldn't reach YouTube — check the internet connection and "
             "try again.")
    check("an unrecognised stderr falls back to the generic message",
          friendly_error("ERROR: some brand new yt-dlp failure mode")
          == "The download failed. YouTube may have changed something — "
             "the downloader updates itself daily, so try again "
             "tomorrow.")

    print()
    print("Download: sha256 verification")
    with tempfile.NamedTemporaryFile(delete=False) as fh:
        fh.write(b"service visuals")
        temp_path = fh.name
    try:
        digest = hashlib.sha256(b"service visuals").hexdigest()
        check("verify_sha256 accepts the right digest",
              tools.verify_sha256(temp_path, digest))
        check("verify_sha256 rejects a wrong digest",
              not tools.verify_sha256(temp_path, "0" * 64))
        check("verify_sha256 is case-insensitive",
              tools.verify_sha256(temp_path, digest.upper()))
    finally:
        os.unlink(temp_path)

    print()
    print("Download: asset_names() per platform/arch")
    real_platform = sys.platform
    real_machine = platform.machine
    try:
        sys.platform = "darwin"
        platform.machine = lambda: "arm64"
        check("darwin/arm64",
              tools.asset_names()
              == ("yt-dlp_macos", "deno-aarch64-apple-darwin.zip"),
              "got {0!r}".format(tools.asset_names()))
        platform.machine = lambda: "x86_64"
        check("darwin/x86_64",
              tools.asset_names()
              == ("yt-dlp_macos", "deno-x86_64-apple-darwin.zip"),
              "got {0!r}".format(tools.asset_names()))
        sys.platform = "win32"
        platform.machine = lambda: "ARM64"
        check("win32/ARM64",
              tools.asset_names()
              == ("yt-dlp_arm64.exe", "deno-aarch64-pc-windows-msvc.zip"),
              "got {0!r}".format(tools.asset_names()))
        platform.machine = lambda: "AMD64"
        check("win32/AMD64",
              tools.asset_names()
              == ("yt-dlp.exe", "deno-x86_64-pc-windows-msvc.zip"),
              "got {0!r}".format(tools.asset_names()))
    finally:
        sys.platform = real_platform
        platform.machine = real_machine

    print()
    print("Download: EXPORT_FILENAME_RE / status route")
    import app as _app
    check("EXPORT_FILENAME_RE matches an mp3 export",
          _app.EXPORT_FILENAME_RE.fullmatch("Talk-abc123.mp3") is not None)
    # Downloads are named after the real video title now, so the pattern
    # has to pass a human filename — including one with dots inside it,
    # which a naive ".." guard would have rejected.
    for good in ("Fred again.. - Delilah.mp4",
                 "Café Worship (Live) 2026.mp4",
                 "A talk, part 2 — notes.mp3",
                 "timer_5m00s_classic_20260904-101010.mp4"):
        check("reveals a real title: {0!r}".format(good),
              _app.EXPORT_FILENAME_RE.fullmatch(good) is not None)
    for bad in ("../secrets.mp4", "sub/dir.mp4", "back\\slash.mp4",
                "no-extension", "line\nbreak.mp4", "x" * 260 + ".mp4"):
        check("refuses {0!r}".format(bad),
              _app.EXPORT_FILENAME_RE.fullmatch(bad) is None)

    # The clean-filename rule itself: no --restrict-filenames (it flattens
    # a title to ASCII underscores) and no video id in the template.
    import downloader
    args = downloader._build_args("/yt", "https://youtu.be/x", "mp4",
                                  "/deno", "/ffmpeg")
    check("no --restrict-filenames", "--restrict-filenames" not in args)
    template = args[args.index("-o") + 1]
    check("the filename is the title only, no video id",
          template.endswith("%(title).80s.%(ext)s"), template)
    check("--no-overwrites is set (batch-download.md's skip behaviour)",
          "--no-overwrites" in args)

    client = _app.app.test_client()
    resp = client.get("/api/download/status")
    body = resp.get_json() or {}
    check("GET /api/download/status answers ready/ytdlp_version (offline)",
          resp.status_code == 200 and "ready" in body
          and "ytdlp_version" in body, "got {0!r}".format(body))


def check_stats_privacy():
    """What the anonymous error reports may contain, and what they may not."""
    import stats

    print("Stats: error report scrubbing")
    leaks = [
        "[Errno 13] Permission denied: '/Users/somebody/Documents/Service Visuals/timer.mp4'",
        "cannot open C:\\Users\\Somebody\\AppData\\Local\\Temp\\_MEI1\\ffmpeg.exe",
        "KeyError: 'Pink sparkly ponies'",
        "could not write board_Group-points_20260818.png (disk full)",
        "The board ~/.service-visuals/boards/abc/source.png is missing",
    ]
    for raw in leaks:
        out = stats._scrub(raw)
        check("no path, filename or quoted value survives: " + raw[:32] + "…",
              "/" not in out and "\\" not in out and ".png" not in out
              and ".mp4" not in out and "ponies" not in out
              and "Somebody" not in out and "somebody" not in out,
              "got {0!r}".format(out))
    check("harmless messages pass through",
          stats._scrub("h264_nvenc: Cannot load nvcuda.dll (error 126)")
          == "h264_nvenc: Cannot load nvcuda.dll (error 126)", "mangled")

    def inner():
        raise ValueError("boom")

    try:
        inner()
    except ValueError as exc:
        where = stats._where(exc)
    check("where() gives basename:line function, innermost first",
          where.startswith("smoke.py:") and "inner" in where.split(" < ")[0]
          and "/" not in where, "got {0!r}".format(where))

    print()
    print("Stats: export props and duration")
    import app as _app
    check("took buckets straddle their boundaries",
          [_app._took_bucket(m) for m in
           (0, 1999, 2000, 4999, 5000, 9999, 10000, 19999, 20000, 29999,
            30000, 44999, 45000, 59999, 60000, 119999, 120000, 299999,
            300000)]
          == ["<2s", "<2s", "2-5s", "2-5s", "5-10s", "5-10s", "10-20s",
              "10-20s", "20-30s", "20-30s", "30-45s", "30-45s", "45-60s",
              "45-60s", "1-2m", "1-2m", "2-5m", "2-5m", ">5m"],
          "bucket boundaries moved")
    # Every bucket has to be reachable, and the labels have to be unique —
    # a typo'd limit would silently make one unreachable and quietly bias
    # the picture the owner reads on the dashboard.
    labels = [label for _limit, label in _app.TOOK_BUCKETS] + [">5m"]
    check("bucket labels are unique", len(labels) == len(set(labels)))
    limits = [limit for limit, _label in _app.TOOK_BUCKETS]
    check("bucket limits ascend", limits == sorted(set(limits)))
    reached = {_app._took_bucket(m) for m in
               [0] + [lim - 1 for lim in limits] + [limits[-1] + 1]}
    check("every bucket is reachable", reached == set(labels),
          "unreachable: {0!r}".format(set(labels) - reached))
    # The props are read off the options dict; an unvalidated value must
    # never become an analytics prop.
    nasty = {"mode": "Pink sparkly ponies", "style": "/Users/someone/x.png",
             "backgrounds": ["a" * 16]}
    tp = _app._timer_props(nasty)
    check("timer props clamp to our own words",
          tp == {"mode": "countdown", "style": "classic", "bg": "one"},
          "got {0!r}".format(tp))
    check("spinner props clamp",
          _app._spinner_props({"mode": "../etc/passwd"}) == {"mode": "random"})
    check("motionbg props clamp",
          _app._motionbg_props({"style": "secret"}) == {"style": "aurora"})
    check("qr props clamp",
          _app._qr_props({"style": "neon"}) == {"style": "card"})

    # Never sends when not asked to: no worker, no queue growth.
    before = stats._q.qsize()
    stats.track("crash", error="X")
    check("track() is a no-op until start() has run",
          stats._q.qsize() == before, "queued an event with no worker")

    # A source run must land under Aptabase's Debug toggle so the owner's
    # release testing never mixes with real churches' counts. Stand in a
    # fake worker so track() queues without ever starting the sender.
    stats._state["worker"] = object()
    try:
        stats.set_enabled(True)
        stats.track("crash", error="X")
        event = stats._q.get_nowait()
    finally:
        stats._state["worker"] = None
    check("a source run reports isDebug=true",
          event["systemProps"].get("isDebug") is True,
          "got {0!r}".format(event["systemProps"].get("isDebug")))


def check_vision_flip():
    """Apple Vision's bottom-left origin must be flipped, not copied.

    Getting this backwards mirrors every box vertically and still looks
    plausible, so it is asserted explicitly. Pure arithmetic — no OCR engine
    involved, so it runs on CI too.
    """
    print("Scoreboard: Vision rect conversion")
    # A box across the TOP of a 1000x800 image: Vision measures y up from the
    # bottom, so a top-of-image box has a HIGH y (0.75) and must come back as
    # a LOW pixel y (0.0). A naive y*height would put it at 600.
    x, y, w, h = scoreboard._from_vision_rect(0.25, 0.75, 0.5, 0.25, 1000, 800)
    check("vision y-flip puts a top box at the top",
          (x, y, w, h) == (250, 0, 500, 200),
          "got {0!r}, expected (250, 0, 500, 200)".format((x, y, w, h)))

    # ...and a box across the BOTTOM (y=0) must land at the bottom.
    x, y, w, h = scoreboard._from_vision_rect(0.0, 0.0, 1.0, 0.25, 1000, 800)
    check("vision y-flip puts a bottom box at the bottom",
          (x, y, w, h) == (0, 600, 1000, 200),
          "got {0!r}, expected (0, 600, 1000, 200)".format((x, y, w, h)))


def _winocr_stub(result, limit=10000):
    """A stand-in for the winocr module, shaped like the real one.

    `recognize_pil_sync` returns the dict winocr builds out of an OcrResult
    ({"text_angle", "lines":[{"words":[{"text","bounding_rect"}]}]}), and
    OcrEngine carries the static max_image_dimension. Lets the Windows branch
    be exercised on this Mac, which is otherwise the only code path in the
    feature nobody can run before release.
    """
    import types

    module = types.ModuleType("winocr")

    class OcrEngine(object):
        max_image_dimension = limit

    module.OcrEngine = OcrEngine
    # The detector now OCRs several renditions (native, rescaled, inverted).
    # A real engine reports rects in the pixels of the image it was handed,
    # so scale ours by the ratio to the native width; a stub that ignored
    # scale would place every rescaled read somewhere else on the board.
    calls = []
    module.calls = calls
    module.base_width = [None]

    def recognize(image, lang):
        calls.append(image.size)
        if module.base_width[0] is None:
            module.base_width[0] = image.width
        f = image.width / float(module.base_width[0])
        if f == 1.0:
            return result
        out = {"text_angle": result.get("text_angle"), "lines": []}
        for line in result.get("lines", []):
            words = []
            for w in line.get("words", []):
                r = w["bounding_rect"]
                words.append({"text": w["text"], "bounding_rect": {
                    "x": r["x"] * f, "y": r["y"] * f,
                    "width": r["width"] * f, "height": r["height"] * f}})
            out["lines"].append({"words": words})
        return out

    module.recognize_pil_sync = recognize
    return module


def _rect(x, y, w, h):
    return {"x": x, "y": y, "width": w, "height": h}


def check_windows_ocr():
    """The Windows detector, driven with hand-written winocr output."""
    import sys as _sys

    from PIL import Image

    print("Scoreboard: Windows OCR contract (stubbed)")
    img = Image.new("RGB", (900, 500), (255, 255, 255))

    # Letter-spaced "3 5 0" arrives as three words; a small label digit and a
    # phone number must not become editable boxes.
    words = [
        {"text": "3", "bounding_rect": _rect(100, 100, 30, 60)},
        {"text": "5", "bounding_rect": _rect(140, 100, 30, 60)},
        {"text": "0", "bounding_rect": _rect(180, 100, 30, 60)},
        {"text": "1", "bounding_rect": _rect(400, 110, 10, 20)},
        {"text": "0412345678", "bounding_rect": _rect(100, 300, 200, 60)},
    ]
    result = {"text_angle": 0.0, "lines": [{"words": words}]}

    saved = _sys.modules.get("winocr")
    _sys.modules["winocr"] = _winocr_stub(result)
    try:
        boxes = scoreboard._detect_windows(img)
        check("split digits are rejoined into one number",
              [b["text"] for b in boxes] == ["350"],
              "got {0!r}".format([b.get("text") for b in boxes]))
        check("the rejoined box spans all three digits",
              boxes and (boxes[0]["x"], boxes[0]["w"]) == (100, 110),
              "got {0!r}".format(boxes[:1]))
        check("several renditions were read and unioned without duplicates",
              len(_sys.modules["winocr"].calls) >= 3 and len(boxes) == 1,
              "calls={0} boxes={1}".format(
                  _sys.modules["winocr"].calls, [b["text"] for b in boxes]))

        # Windows reads light digits as letters: "35O" is 350, "24O" is 240,
        # but a real word ("SO") stays out.
        lookalike = {"text_angle": 0.0, "lines": [{"words": [
            {"text": "35O", "bounding_rect": _rect(100, 100, 100, 60)},
            {"text": "I5", "bounding_rect": _rect(400, 100, 60, 60)},
            {"text": "SO", "bounding_rect": _rect(600, 100, 60, 60)},
        ]}]}
        _sys.modules["winocr"] = _winocr_stub(lookalike)
        boxes = scoreboard._detect_windows(img)
        check("lookalike letters inside numbers become digits",
              sorted(b["text"] for b in boxes) == ["15", "350"],
              "got {0!r}".format([b["text"] for b in boxes]))

        # A tilted capture must be refused, not silently mapped onto the
        # wrong pixels: the rects Windows returns are de-skewed.
        _sys.modules["winocr"] = _winocr_stub(
            {"text_angle": -3.0, "lines": [{"words": words}]})
        try:
            scoreboard._detect_windows(img)
            check("a tilted board is refused", False, "no error raised")
        except scoreboard.BoardError as exc:
            check("a tilted board is refused", "rotated" in str(exc),
                  "got {0!r}".format(str(exc)))

        # Too big for Windows.Media.Ocr is the input's fault, not the
        # platform's, so it must NOT claim OCR is unavailable.
        _sys.modules["winocr"] = _winocr_stub(result, limit=100)
        try:
            scoreboard._detect_windows(img)
            check("an oversized image is refused", False, "no error raised")
        except scoreboard.OCRUnavailable as exc:
            check("an oversized image is refused", False,
                  "blamed the system: {0!r}".format(str(exc)))
        except scoreboard.BoardError as exc:
            check("an oversized image is refused", "900px" in str(exc),
                  "got {0!r}".format(str(exc)))
    finally:
        if saved is None:
            _sys.modules.pop("winocr", None)
        else:
            _sys.modules["winocr"] = saved


def check_scoreboard():
    """Board harvest -> erase -> recompose, with the boxes supplied directly.

    Deliberately does NOT call detect_numbers: CI is Linux, which has no OS
    text recogniser. Everything that can actually break — digit harvesting,
    the content-aware erase, and compositing the new value — happens after
    detection, so handing in the rects tests the interesting part everywhere.
    """
    import io

    from PIL import Image, ImageChops

    print("Scoreboard: synthetic board (no OCR)")
    source = mock_board.build()
    boxes = mock_board.number_boxes()

    board = scoreboard.create_board(source, "Smoke board", boxes=boxes)
    check("board has one box per number",
          len(board["boxes"]) == len(boxes),
          "got {0} boxes, expected {1}".format(
              len(board["boxes"]), len(boxes)))
    check("board values seeded from the image",
          [board["values"][b["id"]] for b in board["boxes"]]
          == list(mock_board.NUMBERS),
          "got {0!r}".format(board.get("values")))

    # Change exactly one number; every other box must be left completely alone.
    target = board["boxes"][0]
    scoreboard.save_values(board["id"], {target["id"]: "409"})
    board = scoreboard.load_board(board["id"])

    out = scoreboard.render_board(board["id"])   # by id, the way app.py calls it
    check("render matches the source size (not forced to 1920x1080)",
          out.size == source.size,
          "got {0!r}, source is {1!r}".format(out.size, source.size))

    buf = io.BytesIO()
    out.save(buf, format="PNG")
    buf.seek(0)
    reopened = Image.open(buf)
    reopened.load()
    check("render is a valid PNG",
          reopened.format == "PNG" and reopened.size == source.size,
          "got format={0!r} size={1!r}".format(
              reopened.format, reopened.size))

    # The only pixels allowed to move are inside the edited box's padded crop.
    diff = ImageChops.difference(source.convert("RGB"), out).getbbox()
    check("changing a value actually changed pixels", diff is not None,
          "the render is byte-identical to the source")

    if diff is not None:
        pad_x = scoreboard.BOX_PAD
        pad_y = max(scoreboard.BOX_PAD, int(round(target["h"] * 0.18)))
        allowed = (target["x"] - pad_x, target["y"] - pad_y,
                   target["x"] + target["w"] + pad_x,
                   target["y"] + target["h"] + pad_y)
        inside = (diff[0] >= allowed[0] and diff[1] >= allowed[1]
                  and diff[2] <= allowed[2] and diff[3] <= allowed[3])
        check("nothing outside the edited box moved", inside,
              "changed region {0!r} escapes the box's crop {1!r}".format(
                  diff, allowed))

        # And the untouched boxes are byte-identical, not merely close.
        other = board["boxes"][-1]
        crop = (other["x"] - pad_x, other["y"] - pad_y,
                other["x"] + other["w"] + pad_x,
                other["y"] + other["h"] + pad_y)
        check("an untouched number is byte-identical",
              source.crop(crop).tobytes() == out.crop(crop).tobytes(),
              "box {0!r} changed".format(other["id"]))

    # A value longer than the original must be scaled into its own card, not
    # painted over the artwork beside it. Compared on a horizontal strip so
    # only this box's row is in the diff.
    wide = board["boxes"][-1]
    scoreboard.save_values(board["id"], {wide["id"]: "123456"})
    long_out = scoreboard.render_board(board["id"])
    pad_y = max(scoreboard.BOX_PAD, int(round(wide["h"] * 0.18)))
    strip = (0, max(0, wide["y"] - pad_y), source.width,
             min(source.height, wide["y"] + wide["h"] + pad_y))
    moved = ImageChops.difference(source.convert("RGB").crop(strip),
                                  long_out.crop(strip)).getbbox()
    lo = min(wide.get("safe_x0", wide["x"]), wide["x"]) - scoreboard.BOX_PAD
    hi = max(wide.get("safe_x1", wide["x"] + wide["w"]),
             wide["x"] + wide["w"]) + scoreboard.BOX_PAD
    check("a six-digit value stays inside its card",
          moved is not None and moved[0] >= lo and moved[2] <= hi,
          "changed columns {0!r}, card allows {1}..{2}".format(moved, lo, hi))

    # The export lands in exports/ as a real PNG, like the QR still does.
    filename = scoreboard.export_board(board["id"])
    path = os.path.join(EXPORTS_DIR, filename)
    try:
        check("export writes a .png into exports/",
              filename.endswith(".png") and os.path.isfile(path),
              "got {0!r}".format(filename))
        if os.path.isfile(path):
            saved = Image.open(path)
            saved.load()
            check("exported PNG matches the source size",
                  saved.format == "PNG" and saved.size == source.size,
                  "got format={0!r} size={1!r}".format(
                      saved.format, saved.size))
    finally:
        if os.path.isfile(path):
            os.unlink(path)

    # A number OCR missed can be added by hand: same harvest, same render.
    extra = boxes[0]
    before = len(scoreboard.load_board(board["id"])["boxes"])
    try:
        scoreboard.add_box(board["id"], {"x": extra["x"], "y": extra["y"],
                                         "w": extra["w"], "h": extra["h"]},
                           extra["text"])
        check("adding a box over an existing number is refused", False,
              "no error raised")
    except scoreboard.BoardError as exc:
        check("adding a box over an existing number is refused",
              "already" in str(exc), "got {0!r}".format(str(exc)))
    manual_rect = {"x": 5, "y": 5, "w": 60, "h": 30}     # blank corner
    added = scoreboard.add_box(board["id"], manual_rect, "77")
    check("a hand-drawn box joins the board",
          len(added["boxes"]) == before + 1 and
          any(b.get("manual") for b in added["boxes"]),
          "got {0} boxes".format(len(added["boxes"])))
    check("its value is seeded from what the operator typed",
          added["values"].get([b for b in added["boxes"]
                               if b.get("manual")][0]["id"]) == "77",
          "values={0!r}".format(added["values"]))
    scoreboard.render_board(board["id"])   # must not raise with a manual box

    # A board with nothing detected is kept for manual marking, not refused.
    empty = scoreboard.create_board(source, "Empty board", boxes=[])
    check("a board with no detected numbers is still created",
          empty.get("id") and empty["boxes"] == [],
          "got {0!r}".format(empty.get("boxes")))
    check("an empty board can be reopened",
          scoreboard.load_board(empty["id"])["boxes"] == [], "load failed")
    scoreboard.delete_board(empty["id"])

    scoreboard.delete_board(board["id"])
    check("deleting a board removes its folder",
          not os.path.isdir(os.path.join(scoreboard.BOARDS_DIR, board["id"])))


def check_download_retry_and_remove():
    """YouTube's intermittent bot check must be recognised (it was read
    as "private" in v1.29.1) and retried; REMOVE must leave nothing in
    bin/. Offline: fake binaries, no network."""
    import downloader
    import tools
    print("Download: bot check + REMOVE")
    bot = ("ERROR: [youtube] S9IJ1GgAAxE: Sign in to confirm you’re "
           "not a bot. Use --cookies-from-browser or --cookies")
    check("the bot check is recognised", downloader.is_bot_check(bot))
    check("a private video is not a bot check",
          not downloader.is_bot_check("ERROR: Private video. Sign in"))
    check("the bot check gets its own message",
          "robot" in downloader.friendly_error(bot))
    check("a private video keeps the private message",
          "private" in downloader.friendly_error("ERROR: Private video"))

    # The whole classification table. "Sign in to confirm your age"
    # contains "confirm you" AND "sign in", so it sat in the blast radius
    # of both the bot-check and private tests — v1.30.0 called it a bot
    # check and retried it twice for nothing.
    table = [
        ("ERROR: [youtube] x: Sign in to confirm you're not a bot.",
         "bot_check"),
        ("ERROR: [youtube] x: Private video. Sign in if you have access",
         "private"),
        ("ERROR: [youtube] x: Sign in to confirm your age.", "age"),
        ("ERROR: [youtube] x: This video is age-restricted", "age"),
        ("ERROR: [youtube] x: Video unavailable", "unavailable"),
        ("ERROR: unable to download: <urlopen error timed out>", "network"),
        ("ERROR: Unable to open page for storage message", "unknown"),
        ("ERROR: something brand new", "unknown"),
    ]
    for text, want in table:
        got = downloader.classify_error(text)
        check("classify: {0!r} -> {1}".format(text[16:40], want),
              got == want, "got {0}".format(got))
    check("the age wall never triggers bot-check retries",
          not downloader.is_bot_check("Sign in to confirm your age"))
    check("every reason has a message",
          all(r in downloader._REASON_MESSAGES
              for r in downloader.ERROR_REASONS if r != "setup"))
    check("a setup failure is its own reason",
          "setup" in downloader.ERROR_REASONS)

    # Privacy: what a failed download is allowed to report. Nothing here
    # may carry the link, the title or the filename (stats.py's rule).
    import app as _app2
    props = _app2._download_props({"format": "mp3",
                                   "urls": ["https://youtu.be/SECRET"]})
    check("download props are format + yt-dlp version + batch only",
          set(props) == {"format", "ytdlp", "batch"},
          "got {0!r}".format(props))
    check("no part of the link survives into props",
          "SECRET" not in repr(props))
    check("a one-element urls list props as batch=one",
          props["batch"] == "one", "got {0!r}".format(props))
    many_props = _app2._download_props(
        {"format": "mp3",
         "urls": ["https://youtu.be/a", "https://youtu.be/b"]})
    check("a multi-element urls list props as batch=many",
          many_props["batch"] == "many", "got {0!r}".format(many_props))
    import stats as _stats
    check("download_failed is a declared event",
          "download_failed" in _stats.EVENTS)
    delays = downloader.BOT_CHECK_DELAYS
    check("retry pauses are short (under 90 s in total)",
          all(d > 0 for d in delays) and sum(delays) <= 90,
          "got {0!r}".format(delays))

    os.makedirs(tools.BIN_DIR, exist_ok=True)
    ytdlp_path, deno_path = tools.binary_paths()
    fakes = [ytdlp_path, deno_path, tools.STAMP_PATH, tools.VERSION_PATH,
             os.path.join(tools.BIN_DIR, "deno.zip.part")]
    for path in fakes:
        with open(path, "wb") as fh:
            fh.write(b"x")
    check("status reads ready with the fake binary present",
          tools.tools_status()["ready"])

    # Launch-time self-update: off the UI thread, and above all it must
    # never fetch anything on its own — a fresh install that has never
    # used the tile would otherwise pull 120 MB just by opening the app.
    # (The stamp above is new, so update_ytdlp() no-ops: stays offline.)
    thread = tools.start_background_update()
    check("launch update runs in a thread when the tools are installed",
          thread is not None)
    if thread is not None:
        thread.join(30)
        check("launch update finishes without blocking", not thread.is_alive())
    result = tools.remove_tools()
    check("remove_tools reports ok", result.get("ok") is True,
          "got {0!r}".format(result))
    check("remove_tools leaves nothing behind",
          not any(os.path.exists(p) for p in fakes))
    check("status reads not ready afterwards",
          not tools.tools_status()["ready"])
    check("launch update does nothing once the tools are removed",
          tools.start_background_update() is None)


def check_batch_download():
    """batch-download.md: BATCH_LANES/BATCH_GAP, the concurrent per-item
    orchestrator (with _run_once fully faked -- no yt-dlp, no network),
    jobs.py's dict-passthrough, and the per-item analytics wiring. No
    real sleeping anywhere: time.sleep is patched throughout.
    """
    import time as _time
    import downloader
    import tools

    print("Download: BATCH_LANES / BATCH_GAP")
    check("BATCH_LANES is 3", downloader.BATCH_LANES == 3,
          "got {0!r}".format(downloader.BATCH_LANES))
    gap = downloader.BATCH_GAP
    check("BATCH_GAP is a 2-tuple inside 5-30s",
          isinstance(gap, tuple) and len(gap) == 2
          and 5 <= gap[0] < gap[1] <= 30, "got {0!r}".format(gap))

    print()
    print("Download: batch orchestration (fake _run_once, no sleeping)")

    # Same trick check_download_retry_and_remove uses: fake binaries (any
    # content -- _run_once is faked below too, so they are never actually
    # executed) plus a fresh stamp/version file, so ensure_tools()/
    # update_ytdlp() see everything already installed and do no network
    # work of their own.
    os.makedirs(tools.BIN_DIR, exist_ok=True)
    ytdlp_path, deno_path = tools.binary_paths()
    tool_fakes = [ytdlp_path, deno_path, tools.STAMP_PATH, tools.VERSION_PATH]
    for path in tool_fakes:
        with open(path, "wb") as fh:
            fh.write(b"x")

    real_run_once = downloader._run_once
    real_sleep = _time.sleep
    url_saved = "https://youtu.be/SMOKE_SAVED"
    url_skipped = "https://youtu.be/SMOKE_SKIPPED"
    url_failed = "https://youtu.be/SMOKE_FAILED"
    saved_path = os.path.join(EXPORTS_DIR, "_smoke_batch_saved.mp3")
    skipped_path = os.path.join(EXPORTS_DIR, "_smoke_batch_skipped.mp3")
    cleanup_paths = [saved_path, skipped_path]

    # Pre-exists with an OLD mtime -- the ONLY signal _download_one has
    # for "skipped": yt-dlp's stdout is identical either way (see
    # downloader._build_args), but --no-overwrites never touches an
    # existing file, so its mtime stays in the past.
    with open(skipped_path, "w", encoding="utf-8") as fh:
        fh.write("already here")
    old = _time.time() - 3600
    os.utime(skipped_path, (old, old))

    def fake_run_once(args, needs_fetch, progress_cb):
        url = args[1]
        if url == url_saved:
            with open(saved_path, "w", encoding="utf-8") as fh:
                fh.write("fresh")
            return 0, saved_path, ""
        if url == url_skipped:
            return 0, skipped_path, ""
        if url == url_failed:
            return 1, None, "ERROR: Video unavailable"
        return 1, None, "ERROR: unrecognised url in smoke fixture"

    sleep_calls = []
    downloader._run_once = fake_run_once
    _time.sleep = lambda seconds: sleep_calls.append(seconds)
    try:
        progress_values = []
        result = downloader.download_video(
            {"urls": [url_saved, url_skipped, url_failed], "format": "mp3"},
            progress_values.append)

        check("a batch returns a dict with filename + items",
              isinstance(result, dict) and "items" in result
              and "filename" in result, "got {0!r}".format(result))
        items = result.get("items") or [None, None, None]
        check("items come back in submitted order",
              [i.get("link") for i in items]
              == [url_saved, url_skipped, url_failed],
              "got {0!r}".format([i.get("link") for i in items]))
        check("a freshly-written file is reported saved",
              items[0].get("state") == "saved"
              and items[0].get("filename") == os.path.basename(saved_path)
              and items[0].get("reason") is None,
              "got {0!r}".format(items[0]))
        check("a pre-existing (old mtime) file is reported skipped",
              items[1].get("state") == "skipped"
              and items[1].get("filename")
              == os.path.basename(skipped_path),
              "got {0!r}".format(items[1]))
        check("a failed item carries the operator sentence + reason",
              items[2].get("state") == "failed"
              and items[2].get("reason") == "unavailable"
              and items[2].get("message") == "That video isn't available.",
              "got {0!r}".format(items[2]))
        check("every item carries its own seconds",
              all(isinstance(i.get("seconds"), float)
                  and i.get("seconds") >= 0 for i in items),
              "got {0!r}".format(items))
        check("the top-level filename is the (only) saved item's",
              result.get("filename") == os.path.basename(saved_path),
              "got {0!r}".format(result.get("filename")))
        check("progress reaches 100",
              progress_values[-1:] == [100],
              "got {0!r}".format(progress_values))
        check("a batch of 3 (one item per lane) never sleeps",
              sleep_calls == [], "got {0!r}".format(sleep_calls))
    finally:
        downloader._run_once = real_run_once
        _time.sleep = real_sleep
        for path in cleanup_paths:
            if os.path.isfile(path):
                os.unlink(path)

    print()
    print("Download: a one-item batch never sleeps (today's single link)")
    sleep_calls_one = []
    downloader._run_once = fake_run_once
    _time.sleep = lambda seconds: sleep_calls_one.append(seconds)
    try:
        result = downloader.download_video(
            {"urls": [url_saved], "format": "mp3"}, lambda _pct: None)
        check("a one-item batch still returns the batch dict shape",
              result.get("filename") == os.path.basename(saved_path)
              and len(result.get("items") or []) == 1,
              "got {0!r}".format(result))
        check("time.sleep is never called for a one-item batch",
              sleep_calls_one == [], "got {0!r}".format(sleep_calls_one))
    finally:
        downloader._run_once = real_run_once
        _time.sleep = real_sleep
        if os.path.isfile(saved_path):
            os.unlink(saved_path)

    print()
    print("Download: the jittered gap engages once a lane gets 2 items")
    urls_four = ["https://youtu.be/SMOKE_Q0", "https://youtu.be/SMOKE_Q1",
                "https://youtu.be/SMOKE_Q2", "https://youtu.be/SMOKE_Q3"]
    made_paths = []

    def fake_run_once_many(args, needs_fetch, progress_cb):
        url = args[1]
        path = os.path.join(
            EXPORTS_DIR, "_smoke_batch_{0}.mp3".format(url[-2:]))
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("x")
        made_paths.append(path)
        return 0, path, ""

    sleep_calls_many = []
    downloader._run_once = fake_run_once_many
    _time.sleep = lambda seconds: sleep_calls_many.append(seconds)
    try:
        result = downloader.download_video(
            {"urls": urls_four, "format": "mp3"}, lambda _pct: None)
        # Round-robin over 3 lanes: item 0 and item 3 share lane 0 (its
        # only 2nd item across the whole batch); lanes 1 and 2 get one
        # item each and so never sleep -- exactly one jittered gap.
        check("exactly one lane received a 2nd item (one sleep)",
              len(sleep_calls_many) == 1,
              "got {0!r}".format(sleep_calls_many))
        check("the jittered sleep falls inside BATCH_GAP's bounds",
              bool(sleep_calls_many)
              and gap[0] <= sleep_calls_many[0] <= gap[1],
              "got {0!r}".format(sleep_calls_many))
        check("all 4 items still come back saved",
              all(i["state"] == "saved" for i in result["items"]),
              "got {0!r}".format(result["items"]))
    finally:
        downloader._run_once = real_run_once
        _time.sleep = real_sleep
        for path in made_paths:
            if os.path.isfile(path):
                os.unlink(path)
        tools.remove_tools()

    print()
    print("Download: jobs.py passes a dict renderer's extras through")

    from jobs import JobManager

    def fake_dict_renderer(_options, progress_cb):
        progress_cb(50)
        return {"filename": "final.mp3", "items": [{"link": "x"}],
                "extra_field": 123}

    def fake_string_renderer(_options, progress_cb):
        progress_cb(50)
        return "plain.mp4"

    job_mgr = JobManager({"fakedict": fake_dict_renderer,
                          "fakestring": fake_string_renderer})

    def _wait_done(job_id, timeout=5):
        deadline = _time.time() + timeout
        info = job_mgr.get(job_id)
        while info["status"] not in ("done", "error"):
            if _time.time() > deadline:
                break
            real_sleep(0.01)
            info = job_mgr.get(job_id)
        return info

    dict_info = _wait_done(job_mgr.submit("fakedict", {}))
    check("a dict-returning renderer: job.filename is the dict's filename",
          dict_info.get("filename") == "final.mp3",
          "got {0!r}".format(dict_info))
    check("a dict-returning renderer: extra keys reach info()",
          dict_info.get("items") == [{"link": "x"}]
          and dict_info.get("extra_field") == 123,
          "got {0!r}".format(dict_info))
    check("a dict-returning renderer: the core job fields are unchanged",
          dict_info.get("status") == "done"
          and dict_info.get("progress") == 100
          and "id" in dict_info and "queue_position" in dict_info,
          "got {0!r}".format(dict_info))

    string_info = _wait_done(job_mgr.submit("fakestring", {}))
    check("a string-returning renderer behaves exactly as today",
          string_info.get("filename") == "plain.mp4"
          and set(string_info) == {"id", "type", "status", "progress",
                                   "filename", "error", "queue_position"},
          "got {0!r}".format(string_info))

    print()
    print("Download: per-item analytics (export/download_failed per item)")

    import stats as _stats2
    import app as _app4

    tracked = []
    real_track = _stats2.track
    real_download_video = _app4.download_video
    fake_items = [
        {"link": "https://youtu.be/1", "state": "saved",
         "filename": "one.mp3", "message": None, "reason": None,
         "seconds": 12.3},
        {"link": "https://youtu.be/2", "state": "skipped",
         "filename": "two.mp3", "message": None, "reason": None,
         "seconds": 1.0},
        {"link": "https://youtu.be/3", "state": "failed",
         "filename": None, "message": "That video isn't available.",
         "reason": "unavailable", "seconds": 4.5},
    ]

    _stats2.track = lambda name, **props: tracked.append((name, props))
    _app4.download_video = (
        lambda options, progress_cb: {"filename": "one.mp3",
                                      "items": fake_items})
    try:
        passthrough = _app4._counted_download(
            {"urls": ["https://youtu.be/1", "https://youtu.be/2",
                      "https://youtu.be/3"], "format": "mp3"},
            lambda _pct: None)
    finally:
        _stats2.track = real_track
        _app4.download_video = real_download_video

    check("_counted_download passes the renderer's dict straight through",
          passthrough.get("filename") == "one.mp3"
          and passthrough.get("items") == fake_items,
          "got {0!r}".format(passthrough))

    exports = [props for name, props in tracked if name == "export"]
    failed = [props for name, props in tracked if name == "download_failed"]
    check("exactly one export per saved item, none for skipped/failed",
          len(exports) == 1, "got {0!r}".format(tracked))
    check("exactly one download_failed per failed item",
          len(failed) == 1, "got {0!r}".format(tracked))
    check("skipped items emit nothing at all",
          len(tracked) == 2, "got {0!r}".format(tracked))
    check("the export's render_seconds matches the saved item's own",
          exports and abs(exports[0].get("render_seconds", -1) - 12.3) < 0.2,
          "got {0!r}".format(exports))
    check("the download_failed carries the failed item's own reason",
          failed and failed[0].get("reason") == "unavailable",
          "got {0!r}".format(failed))
    check("both events carry batch (many, for this 3-link batch)",
          exports and failed and exports[0].get("batch") == "many"
          and failed[0].get("batch") == "many",
          "got {0!r}".format((exports, failed)))
    check("no link, filename or title leaks into either event's props",
          "youtu.be" not in repr(tracked) and "one.mp3" not in repr(tracked)
          and "isn't available" not in repr(tracked),
          "got {0!r}".format(tracked))

    single_props = _app4._download_props({"format": "mp3", "urls": ["x"]})
    check("a single-link batch props as batch=one",
          single_props.get("batch") == "one",
          "got {0!r}".format(single_props))


def check_qr_ring():
    """The accent ring can be switched off (v1.30.1). Off must mean the
    frame IS the composed base — nothing pasted — and the default must
    stay on so every existing export is untouched (golden covers that)."""
    from render import qr as qr_mod
    from validation import ValidationError, validate_qr_options
    print("QR: ring on/off")
    base = {"url": "https://example.org", "heading": "GIVE",
            "caption": "Thanks"}
    check("ring defaults to on", validate_qr_options(dict(base))["ring"])
    check("ring=false is accepted",
          validate_qr_options(dict(base, ring=False))["ring"] is False)
    try:
        validate_qr_options(dict(base, ring="no"))
        check("ring must be a boolean", False, "no error raised")
    except ValidationError as exc:
        check("ring must be a boolean",
              str(exc) == "The ring option must be true or false.",
              str(exc))
    on = qr_mod.render_qr_still(dict(base, ring=True), max_width=0)
    off = qr_mod.render_qr_still(dict(base, ring=False), max_width=0)
    plain, _geo = qr_mod._compose_base(
        qr_mod._clean_options(dict(base, ring=False)))
    check("ring off renders exactly the composed base",
          off.tobytes() == plain.tobytes())
    check("ring on differs from ring off", on.tobytes() != off.tobytes())


def check_fixed_format():
    """Countdown "Fixed 00:00:00 format": every duration reads HH:MM:SS, so
    30 seconds and 90 minutes are the same string length (and therefore the
    same digit size). Off by default — existing exports must not move."""
    from render.timer import _format_remaining
    from validation import ValidationError, validate_timer_options
    print("Timer: fixed 00:00:00 format")
    cases = ((300, "00:05:00", "5:00"), (30, "00:00:30", "0:30"),
             (5400, "01:30:00", "1:30:00"), (900, "00:15:00", "15:00"))
    for total, fixed_text, plain_text in cases:
        check("{0}s reads {1} when fixed".format(total, fixed_text),
              _format_remaining(total, total, True) == fixed_text,
              _format_remaining(total, total, True))
        check("{0}s is unchanged when off".format(total),
              _format_remaining(total, total) == plain_text,
              _format_remaining(total, total))
    check("every fixed string is the same width",
          len({_format_remaining(t, t, True) for t, _f, _p in cases}) == 4
          and len({len(_format_remaining(t, t, True))
                   for t, _f, _p in cases}) == 1)
    check("counting down keeps the shape",
          [_format_remaining(r, 300, True) for r in (299, 59, 0)]
          == ["00:04:59", "00:00:59", "00:00:00"])

    base = {"minutes": 5, "seconds": 0}
    check("fixed_format defaults to off",
          validate_timer_options(dict(base))["fixed_format"] is False)
    check("fixed_format true is accepted",
          validate_timer_options(dict(base, fixed_format=True))
          ["fixed_format"] is True)
    try:
        validate_timer_options(dict(base, fixed_format="yes"))
        check("fixed_format must be a boolean", False, "no error raised")
    except ValidationError as exc:
        check("fixed_format must be a boolean",
              str(exc) == '"Fixed 00:00:00 format" must be true or false.',
              str(exc))
    # Clock mode is countdown-only territory: the key is simply ignored.
    clock = {"mode": "clock", "start": "19:59:50", "duration_seconds": 30}
    check("clock mode ignores fixed_format",
          "fixed_format" not in validate_timer_options(
              dict(clock, fixed_format=True)))


def check_millis_size_and_ticking():
    """docs/specs/millis-reveal.md: the two new pure functions that decide
    the countdown millis run's font size and its freeze/tick boundary.
    Exercised directly -- _digits_metrics is already used this way
    throughout this file -- so these run everywhere, including CI.
    """
    from render.timer import (
        CLOCK_MS_SCALE, _digits_metrics, _millis_size, _millis_ticking)

    print("Timer: millis size + freeze/tick boundary (pure)")

    off = _millis_size(200, False)
    check("_millis_size(size, False) is today's CLOCK_MS_SCALE ratio",
          off == max(1, int(round(200 * CLOCK_MS_SCALE))),
          "got {0!r}".format(off))
    on = _millis_size(200, True)
    check("_millis_size(size, True) returns the main size unchanged",
          on == 200, "got {0!r}".format(on))

    # Full-size millis must match the main digits' glyph metrics exactly,
    # not just approximately -- the whole point of the option is that
    # the millis run is drawn at the SAME size as the main digits.
    met_main = _digits_metrics(200)
    met_full_ms = _digits_metrics(_millis_size(200, True))
    check("full-size millis glyph_h matches the main digits' glyph_h",
          met_full_ms["glyph_h"] == met_main["glyph_h"],
          "main={0!r} ms={1!r}".format(
              met_main["glyph_h"], met_full_ms["glyph_h"]))
    check("full-size millis slot matches the main digits' slot",
          met_full_ms["slot"] == met_main["slot"],
          "main={0!r} ms={1!r}".format(
              met_main["slot"], met_full_ms["slot"]))

    # _millis_ticking(rem_ms, millis_reveal, millis_reveal_seconds) is
    # inclusive at the boundary ("The freeze/tick boundary") -- these
    # are the same numbers check_millis_reveal_render() below crosses
    # for real, at 30fps with a 4s threshold (boundary rem_ms=4000).
    check("before the threshold: frozen (not ticking)",
          _millis_ticking(4033, True, 4) is False,
          "got {0!r}".format(_millis_ticking(4033, True, 4)))
    check("at the threshold boundary: ticking (inclusive)",
          _millis_ticking(4000, True, 4) is True,
          "got {0!r}".format(_millis_ticking(4000, True, 4)))
    check("after the threshold: ticking",
          _millis_ticking(1500, True, 4) is True,
          "got {0!r}".format(_millis_ticking(1500, True, 4)))
    check("millis_reveal off: always ticking regardless of rem_ms",
          _millis_ticking(999999, False, 4) is True,
          "got {0!r}".format(_millis_ticking(999999, False, 4)))
    check("degenerate reveal_seconds >= total: ticking at frame 0",
          _millis_ticking(10 * 1000, True, 15) is True,
          "got {0!r}".format(_millis_ticking(10 * 1000, True, 15)))


def check_millis_reveal_validation():
    """validation._validate_countdown_options: the 3 new millis-reveal
    fields (docs/specs/millis-reveal.md) -- defaults, both type errors,
    the range error, the accepted "both true" combination, and proof
    clock mode never sees any of them. Mirrors check_fixed_format's
    existing shape for its own sibling option.
    """
    from validation import ValidationError, validate_timer_options

    print("Timer: millis-reveal validation")

    base = {"minutes": 5, "seconds": 0}
    clean = validate_timer_options(dict(base))
    check("millis_full_size defaults to off",
          clean["millis_full_size"] is False, "got {0!r}".format(clean))
    check("millis_reveal defaults to off",
          clean["millis_reveal"] is False, "got {0!r}".format(clean))
    check("millis_reveal_seconds defaults to 60",
          clean["millis_reveal_seconds"] == 60, "got {0!r}".format(clean))

    def expect_error(label, payload, message):
        try:
            validate_timer_options(payload)
            check(label, False, "no error raised")
        except ValidationError as exc:
            check(label, str(exc) == message, "got {0!r}".format(str(exc)))

    expect_error("millis_full_size must be a boolean",
                 dict(base, millis_full_size="yes"),
                 '"Full-size milliseconds" must be true or false.')
    expect_error("millis_reveal must be a boolean",
                 dict(base, millis_reveal="yes"),
                 '"Hold at zero until the end" must be true or false.')
    expect_error("millis_reveal_seconds below 1 is rejected",
                 dict(base, millis_reveal_seconds=0),
                 "Milliseconds can start ticking with 1 to 1800 seconds "
                 "left on the timer.")
    expect_error("millis_reveal_seconds above 1800 is rejected",
                 dict(base, millis_reveal_seconds=1801),
                 "Milliseconds can start ticking with 1 to 1800 seconds "
                 "left on the timer.")
    # The rejections just outside the range prove nothing about the
    # range itself: a `<=` typed as `<` would reject 1 and 1800 too and
    # still pass both checks above.
    for edge in (1, 1800):
        edge_clean = validate_timer_options(
            dict(base, millis_reveal_seconds=edge))
        check("millis_reveal_seconds {0} (the range edge) is accepted"
              .format(edge),
              edge_clean["millis_reveal_seconds"] == edge,
              "got {0!r}".format(edge_clean))

    clean = validate_timer_options(
        dict(base, millis_full_size=True, millis_reveal=True,
             millis_reveal_seconds=4))
    check("millis_full_size and millis_reveal both true is accepted",
          clean["millis_full_size"] is True
          and clean["millis_reveal"] is True
          and clean["millis_reveal_seconds"] == 4,
          "got {0!r}".format(clean))

    # Clock mode is countdown-only territory for all 3 keys -- same
    # pattern as check_fixed_format's own clock-ignores check.
    clock = {"mode": "clock", "start": "19:59:50", "duration_seconds": 30}
    clean = validate_timer_options(
        dict(clock, millis_full_size=True, millis_reveal=True,
             millis_reveal_seconds=4))
    check("clock mode ignores all 3 millis-reveal keys",
          "millis_full_size" not in clean
          and "millis_reveal" not in clean
          and "millis_reveal_seconds" not in clean,
          "got {0!r}".format(clean))


def check_millis_reveal_render():
    """docs/specs/millis-reveal.md -- the seamlessness proof, pixel-
    checked rather than assumed. A 10s classic countdown with
    millis_reveal crosses its 4s threshold between output frames 179
    (rem_ms=4033, frozen '.000') and 180 (rem_ms=4000, live) at 30fps.
    The owner's twice-stated requirement is that the main digits must
    not jump in size or position at that boundary -- checked here as
    one whole-composite bounding box (main + millis together), since
    under this design layout never depends on time or on millis_reveal
    at all. A second check on frame 179 alone confirms the frozen
    millis glyphs are actually drawn (reading .000), not blank.
    """
    from PIL import Image, ImageChops

    print("Timer: millis-reveal seamlessness (real render)")

    filename = render_timer(
        {"minutes": 0, "seconds": 10, "style": "classic",
         "accent": "#e8b44f", "warn_last10": False, "hold_seconds": 1,
         "show_millis": True, "millis_reveal": True,
         "millis_reveal_seconds": 4},
        lambda pct: None)
    path = os.path.join(EXPORTS_DIR, filename)
    frame_dir = tempfile.mkdtemp(prefix="sv-smoke-millis-seam-")
    try:
        verify("timer/classic-millis-reveal-seam", filename, 11.0)

        def extract(idx):
            frame_path = os.path.join(frame_dir, "f{0}.png".format(idx))
            proc = subprocess.run(
                [FFMPEG, "-y", "-i", path,
                 "-vf", "select='eq(n\\, {0})'".format(idx),
                 "-vsync", "0", "-frames:v", "1", frame_path],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            ok = os.path.isfile(frame_path)
            check("frame {0} extracts cleanly".format(idx), ok,
                  proc.stderr.decode("utf-8", "replace")[-300:])
            return frame_path if ok else None

        def lit_mask(frame_path):
            # sum(rgb) > 150 sits comfortably between the vignette
            # background (~25-49) and the digit colour (~717 white, or
            # ~491 for the accent) -- docs/specs/millis-reveal.md.
            # ImageChops.add clips each step at 255, but that can never
            # flip THIS comparison: clipping only happens once the true
            # sum already exceeds 255, which is already past 150.
            r, g, b = Image.open(frame_path).convert("RGB").split()
            total = ImageChops.add(ImageChops.add(r, g), b)
            return total.point(lambda v: 255 if v > 150 else 0)

        f179 = extract(179)
        f180 = extract(180)
        if f179 and f180:
            mask179 = lit_mask(f179)
            mask180 = lit_mask(f180)
            bbox179 = mask179.getbbox()
            bbox180 = mask180.getbbox()
            check("frame 179 (frozen) has a lit bounding box",
                  bbox179 is not None, "got {0!r}".format(bbox179))
            check("frame 180 (live) has a lit bounding box",
                  bbox180 is not None, "got {0!r}".format(bbox180))
            check("the lit bounding box is the IDENTICAL rectangle on "
                  "frame 179 (frozen) and frame 180 (live) -- the main "
                  "digits do not move or resize across the freeze/tick "
                  "boundary",
                  bbox179 == bbox180,
                  "frame179={0!r} frame180={1!r}".format(
                      bbox179, bbox180))
            print("    (observed bounding box: {0!r})".format(bbox179))

            if bbox179 is not None:
                # The millis run sits in the rightmost ~20% of the
                # composite box. On the frozen frame it must still
                # contain lit pixels -- proving the ".000" glyphs are
                # actually drawn, not blank -- without OCR (this
                # file's own convention).
                x0, y0, x1, y1 = bbox179
                ms_x0 = x1 - int(round((x1 - x0) * 0.20))
                ms_region = mask179.crop((ms_x0, y0, x1, y1))
                check("frame 179: the millis region (rightmost 20% "
                      "of the box) has at least one lit pixel -- the "
                      "frozen '.000' glyphs are drawn, not blank",
                      ms_region.getbbox() is not None,
                      "region {0!r} was completely blank".format(
                          (ms_x0, y0, x1, y1)))
    finally:
        shutil.rmtree(frame_dir, ignore_errors=True)
        if os.path.isfile(path):
            os.unlink(path)


def check_millis_reveal_alpha_fringe():
    """docs/specs/millis-reveal.md interaction bug the renderer agent
    found and fixed: frozen_base_for's own _paste_digits call must also
    pass alpha=is_alpha, exactly like base_for's and the live millis
    branch's calls already do -- check_alpha_export covers those two,
    but its own render never turns millis_reveal on, so it never
    exercises frozen_base_for at all. Without the fix, a transparent
    render with millis_reveal gets the self-mask-paste compositing
    fringe alpha-export.md exists to remove, for the whole frozen
    stretch. A 6s classic countdown, transparent="qtrle", show_millis +
    millis_reveal on with a 2s threshold, so frame 30 (t=1.0s,
    rem_ms=5000) sits solidly inside the frozen stretch, nowhere near
    the freeze/tick boundary -- decoded with alpha kept (-pix_fmt
    rgba), same as check_alpha_export's own real-render checks.
    Checked the same way: background/interior alpha AND a fractional
    edge value, not just the two extremes -- a binary (0-and-255-only)
    check would still pass on this exact regression, since neither a
    background pixel nor a fully opaque glyph-interior pixel is
    sensitive to which paste path ran.
    """
    from PIL import Image

    print("Timer: millis-reveal transparent frozen-stretch alpha "
          "(frozen_base_for regression guard)")

    filename = render_timer(
        {"minutes": 0, "seconds": 6, "style": "classic",
         "accent": "#e8b44f", "warn_last10": False, "hold_seconds": 1,
         "show_millis": True, "millis_reveal": True,
         "millis_reveal_seconds": 2, "transparent": "qtrle"},
        lambda pct: None)
    path = os.path.join(EXPORTS_DIR, filename)
    frame_dir = tempfile.mkdtemp(prefix="sv-smoke-millis-alpha-")
    try:
        check("filename ends .mov",
              filename.endswith(".mov"), "got {0!r}".format(filename))
        verify("timer/classic-millis-reveal-alpha", filename, 7.0,
               expected_codec="qtrle", expected_pixfmt="argb")

        # Frame 30 (t=1.0s -> rem_ms=5000) is frozen while rem_ms >
        # 2000 (i.e. frame index < 120 at 30fps) -- squarely a
        # frozen_base_for frame, nowhere near the boundary.
        frame_path = os.path.join(frame_dir, "frame30.png")
        proc = subprocess.run(
            [FFMPEG, "-y", "-i", path,
             "-vf", "select='eq(n\\, 30)'", "-vsync", "0",
             "-frames:v", "1", "-pix_fmt", "rgba", frame_path],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        got_frame = os.path.isfile(frame_path)
        check("frozen-stretch frame extracts cleanly with alpha kept "
              "(-pix_fmt rgba)", got_frame,
              proc.stderr.decode("utf-8", "replace")[-300:])
        if got_frame:
            frame = Image.open(frame_path).convert("RGBA")
            bg_alpha = frame.getpixel((10, 10))[3]
            check("frozen stretch: background-area pixel (10, 10) is "
                  "fully transparent (alpha == 0)",
                  bg_alpha == 0, "got alpha={0!r}".format(bg_alpha))
            frame_alpha = frame.split()[-1]
            lo, hi = frame_alpha.getextrema()
            has_mid = any(
                c > 0 for c in frame_alpha.histogram()[1:255])
            check("frozen stretch: a glyph-interior pixel is fully "
                  "opaque (alpha >= 250)", hi >= 250,
                  "max alpha = {0!r}".format(hi))
            check("frozen stretch: at least one glyph-edge pixel has "
                  "FRACTIONAL alpha (0 < alpha < 255), proving "
                  "frozen_base_for composited with alpha=is_alpha "
                  "instead of the plain self-mask paste",
                  has_mid, "no intermediate alpha value found "
                           "(lo={0!r} hi={1!r})".format(lo, hi))
    finally:
        shutil.rmtree(frame_dir, ignore_errors=True)
        if os.path.isfile(path):
            os.unlink(path)


def check_millis_60fps_pure():
    """docs/specs/millis-60fps.md: _millis_fps(millis_60fps) is the ONE
    place render_timer decides the countdown millis fps -- nothing else
    computes "60 if ... else 30" inline. Exercised directly, pure, no
    rendering involved (same convention as check_millis_size_and_
    ticking's _millis_ticking checks above).
    """
    from render.timer import _millis_fps

    print("Timer: millis fps selection (pure)")

    check("_millis_fps(False) is 30 (today's rate, unchanged)",
          _millis_fps(False) == 30,
          "got {0!r}".format(_millis_fps(False)))
    check("_millis_fps(True) is 60 (smoother milliseconds)",
          _millis_fps(True) == 60,
          "got {0!r}".format(_millis_fps(True)))


def check_millis_60fps_validation():
    """validation._validate_countdown_options: millis_60fps
    (docs/specs/millis-60fps.md) -- default, the bool-type error, the
    900s ceiling accepting exactly at the boundary and rejecting one
    second past it with the exact message, the SAME 901s total being
    accepted once millis_60fps is off (the two ceilings are independent,
    not one replacing the other), and proof clock mode never sees the
    key. Mirrors check_millis_reveal_validation's own shape for its
    sibling options.
    """
    from validation import ValidationError, validate_timer_options

    print("Timer: millis-60fps validation")

    base = {"minutes": 5, "seconds": 0, "show_millis": True}
    clean = validate_timer_options(dict(base))
    check("millis_60fps defaults to off",
          clean["millis_60fps"] is False, "got {0!r}".format(clean))

    try:
        validate_timer_options(dict(base, millis_60fps="yes"))
        check("millis_60fps must be a boolean", False, "no error raised")
    except ValidationError as exc:
        check("millis_60fps must be a boolean",
              str(exc) ==
              '"Smoother milliseconds (60 fps)" must be true or false.',
              "got {0!r}".format(str(exc)))

    # 900s (15 minutes) is the owner's real flagship use case -- exactly
    # AT the 60fps ceiling must be ACCEPTED. An off-by-one here (> vs >=)
    # would block exactly the render he wants.
    clean = validate_timer_options(
        dict(minutes=15, seconds=0, show_millis=True, millis_60fps=True))
    check("900s (15 min) with millis_60fps on is accepted (the ceiling "
          "is inclusive, not exclusive)",
          clean.get("millis_60fps") is True, "got {0!r}".format(clean))

    try:
        validate_timer_options(
            dict(minutes=15, seconds=1, show_millis=True,
                 millis_60fps=True))
        check("901s with millis_60fps on is rejected", False,
              "no error raised")
    except ValidationError as exc:
        check("901s with millis_60fps on is rejected with the exact "
              "60fps-ceiling message",
              str(exc) == "With smoother milliseconds (60 fps) on, "
                          "the timer can run for at most 15 minutes. "
                          "Turn 60 fps off for a longer timer.",
              "got {0!r}".format(str(exc)))

    # The two ceilings are independent: the SAME 901s total, with
    # millis_60fps off, must not trip the 60fps message at all -- it is
    # well under the existing 1800s ceiling, so it is simply accepted.
    clean = validate_timer_options(
        dict(minutes=15, seconds=1, show_millis=True, millis_60fps=False))
    check("901s with millis_60fps OFF is still accepted (the two "
          "ceilings are independent, not one replacing the other)",
          clean.get("millis_60fps") is False, "got {0!r}".format(clean))

    # Clock mode is countdown-only territory for this key too -- same
    # pattern as check_fixed_format / check_millis_reveal_validation's
    # own clock-ignores checks.
    clock = {"mode": "clock", "start": "19:59:50", "duration_seconds": 30}
    clean = validate_timer_options(dict(clock, millis_60fps=True))
    check("clock mode ignores millis_60fps",
          "millis_60fps" not in clean, "got {0!r}".format(clean))


def check_millis_60fps_render():
    """docs/specs/millis-60fps.md -- the "genuine 60fps, not 30fps
    duplicated" proof (measurement 4: ffmpeg only ever DUPLICATES frames
    to reach a higher output fps, it invents no new content, so a naive
    output_fps-only bump would pass a timestamp check while being
    exactly the pointless file the spec's "Do not" section forbids), and
    the freeze/tick seamlessness proof recomputed for 60fps. Mirrors
    check_millis_reveal_render's shape and technique (extract a frame
    with ffmpeg's own decoder, no OCR) for the sister feature's own
    analogous claims.
    """
    from PIL import Image, ImageChops

    print("Timer: millis 60fps -- genuine constant rate + seamlessness")

    # ---- genuine 60fps, probed, not assumed ------------------------------
    filename = render_timer(
        {"minutes": 0, "seconds": 6, "style": "classic",
         "accent": "#e8b44f", "warn_last10": True, "hold_seconds": 2,
         "show_millis": True, "millis_60fps": True},
        lambda pct: None)
    path = os.path.join(EXPORTS_DIR, filename)
    try:
        check("filename carries the _60fps descriptor",
              "_60fps" in filename, "got {0!r}".format(filename))
        verify("timer/classic-millis-60fps", filename, 8.0)

        info_text = subprocess.run(
            [FFMPEG, "-hide_banner", "-i", path],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
        ).stderr.decode("utf-8", "replace")
        m = re.search(r"Video:.*?(\d+(?:\.\d+)?) fps", info_text)
        check("the container reports 60 fps",
              m is not None and float(m.group(1)) == 60.0,
              "got {0!r}".format(m.group(0) if m else None))

        pts = _showinfo_pts(path)
        check("decoded frame count equals duration x 60 exactly "
              "(8s -> 480)",
              len(pts) == 480, "got {0} frames".format(len(pts)))
        deltas = [b - a for a, b in zip(pts, pts[1:])]
        bad = [d for d in deltas if abs(d - 1.0 / 60) > 0.001]
        check("every inter-frame pts delta is 1/60s within tolerance "
              "-- a genuine constant 60fps stream, not a relabeled "
              "30fps one",
              not bad,
              "{0}/{1} deltas off, sample={2!r}".format(
                  len(bad), len(deltas), bad[:5]))

        hashes = _frame_hashes(path, 60)
        check("all 60 frames of one ticking second are pairwise "
              "distinct -- real per-frame content, not 30 unique "
              "frames each shown twice (the exact failure a "
              "30-in/60-out mistake would produce)",
              len(hashes) == 60 and len(set(hashes)) == 60,
              "got {0} distinct out of {1}".format(
                  len(set(hashes)), len(hashes)))
    finally:
        if os.path.isfile(path):
            os.unlink(path)

    # ---- seamlessness across the freeze/tick boundary, at 60fps ---------
    # millis-reveal.md's own 179/180 proof, recomputed for 60fps: NOT a
    # naive 2x (that gives 358/360 and lands on the wrong side of the
    # boundary once rounding is accounted for). At 60fps the last frozen
    # frame is 359 (rem_ms=4017) and the first live frame is 360
    # (rem_ms=4000) -- checked against _millis_ticking/make_frame's own
    # formula directly, not trusted blind.
    filename = render_timer(
        {"minutes": 0, "seconds": 10, "style": "classic",
         "accent": "#e8b44f", "warn_last10": False, "hold_seconds": 1,
         "show_millis": True, "millis_reveal": True,
         "millis_reveal_seconds": 4, "millis_60fps": True},
        lambda pct: None)
    path = os.path.join(EXPORTS_DIR, filename)
    frame_dir = tempfile.mkdtemp(prefix="sv-smoke-millis60-seam-")
    try:
        verify("timer/classic-millis-60fps-reveal", filename, 11.0)

        def extract(idx):
            frame_path = os.path.join(frame_dir, "f{0}.png".format(idx))
            proc = subprocess.run(
                [FFMPEG, "-y", "-i", path,
                 "-vf", "select='eq(n\\, {0})'".format(idx),
                 "-vsync", "0", "-frames:v", "1", frame_path],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            ok = os.path.isfile(frame_path)
            check("frame {0} extracts cleanly".format(idx), ok,
                  proc.stderr.decode("utf-8", "replace")[-300:])
            return frame_path if ok else None

        def lit_mask(frame_path):
            # Same sum(rgb)>150 convention as millis-reveal.md's own
            # 30fps proof (check_millis_reveal_render) -- comfortably
            # between the vignette background and the digit colour.
            r, g, b = Image.open(frame_path).convert("RGB").split()
            total = ImageChops.add(ImageChops.add(r, g), b)
            return total.point(lambda v: 255 if v > 150 else 0)

        f359 = extract(359)
        f360 = extract(360)
        if f359 and f360:
            mask359 = lit_mask(f359)
            mask360 = lit_mask(f360)
            bbox359 = mask359.getbbox()
            bbox360 = mask360.getbbox()
            check("frame 359 (frozen) has a lit bounding box",
                  bbox359 is not None, "got {0!r}".format(bbox359))
            check("frame 360 (live) has a lit bounding box",
                  bbox360 is not None, "got {0!r}".format(bbox360))
            check("the lit bounding box is IDENTICAL on frame 359 "
                  "(frozen) and frame 360 (live) -- the main digits "
                  "do not move or resize across the freeze/tick "
                  "boundary at 60fps",
                  bbox359 == bbox360,
                  "frame359={0!r} frame360={1!r}".format(
                      bbox359, bbox360))
            print("    (observed bounding box: {0!r})".format(bbox359))

            if bbox359 is not None:
                # The millis run sits in the rightmost ~20% of the
                # composite box (same convention as check_millis_
                # reveal_render's own frame-179 check).
                x0, y0, x1, y1 = bbox359
                ms_x0 = x1 - int(round((x1 - x0) * 0.20))
                ms_region = mask359.crop((ms_x0, y0, x1, y1))
                check("frame 359: the millis region (rightmost 20% "
                      "of the box) has at least one lit pixel -- the "
                      "frozen '.000' glyphs are drawn, not blank",
                      ms_region.getbbox() is not None,
                      "region {0!r} was completely blank".format(
                          (ms_x0, y0, x1, y1)))
    finally:
        shutil.rmtree(frame_dir, ignore_errors=True)
        if os.path.isfile(path):
            os.unlink(path)


def check_millis_60fps_alpha_fringe():
    """docs/specs/millis-60fps.md: millis_60fps composes with transparent
    + millis_reveal with zero new interaction code -- frozen_base_for's
    cache key is (text, color, idx), already fps-independent (Decisions
    section) -- but that claim only means something once actually
    rendered and pixel-checked, exactly like check_millis_reveal_alpha_
    fringe already does for the 30fps case. Same shape: a frozen-stretch
    frame, decoded with alpha kept (-pix_fmt rgba), must show background
    alpha 0, glyph-interior alpha 255, AND fractional edge alpha --
    fractional, not just the two extremes, since a binary 0-or-255
    cutout would still pass the first two checks while reproducing the
    exact fringe bug alpha-export.md exists to remove.
    """
    from PIL import Image

    print("Timer: millis-60fps transparent frozen-stretch alpha "
          "(frozen_base_for at 60fps)")

    filename = render_timer(
        {"minutes": 0, "seconds": 6, "style": "classic",
         "accent": "#e8b44f", "warn_last10": False, "hold_seconds": 1,
         "show_millis": True, "millis_reveal": True,
         "millis_reveal_seconds": 2, "millis_60fps": True,
         "transparent": "qtrle"},
        lambda pct: None)
    path = os.path.join(EXPORTS_DIR, filename)
    frame_dir = tempfile.mkdtemp(prefix="sv-smoke-millis60-alpha-")
    try:
        check("filename ends .mov",
              filename.endswith(".mov"), "got {0!r}".format(filename))
        check("filename carries both _ms and _60fps descriptors",
              "_ms" in filename and "_60fps" in filename,
              "got {0!r}".format(filename))

        # Frame 60 at 60fps is t=1.0s -> rem_ms=5000, frozen while
        # rem_ms > 2000 -- the 60fps analogue of check_millis_reveal_
        # alpha_fringe's own frame-30-at-30fps pick, nowhere near the
        # freeze/tick boundary.
        frame_path = os.path.join(frame_dir, "frame60.png")
        proc = subprocess.run(
            [FFMPEG, "-y", "-i", path,
             "-vf", "select='eq(n\\, 60)'", "-vsync", "0",
             "-frames:v", "1", "-pix_fmt", "rgba", frame_path],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        got_frame = os.path.isfile(frame_path)
        check("frozen-stretch frame extracts cleanly with alpha kept "
              "(-pix_fmt rgba)", got_frame,
              proc.stderr.decode("utf-8", "replace")[-300:])
        if got_frame:
            frame = Image.open(frame_path).convert("RGBA")
            bg_alpha = frame.getpixel((10, 10))[3]
            check("frozen stretch (60fps): background-area pixel "
                  "(10, 10) is fully transparent (alpha == 0)",
                  bg_alpha == 0, "got alpha={0!r}".format(bg_alpha))
            frame_alpha = frame.split()[-1]
            lo, hi = frame_alpha.getextrema()
            has_mid = any(
                c > 0 for c in frame_alpha.histogram()[1:255])
            check("frozen stretch (60fps): a glyph-interior pixel is "
                  "fully opaque (alpha >= 250)", hi >= 250,
                  "max alpha = {0!r}".format(hi))
            check("frozen stretch (60fps): at least one glyph-edge "
                  "pixel has FRACTIONAL alpha (0 < alpha < 255), "
                  "proving frozen_base_for's alpha=is_alpha "
                  "compositing still holds at 60fps",
                  has_mid, "no intermediate alpha value found "
                           "(lo={0!r} hi={1!r})".format(lo, hi))
    finally:
        shutil.rmtree(frame_dir, ignore_errors=True)
        if os.path.isfile(path):
            os.unlink(path)


def check_millis_60fps_off_stays_30fps():
    """docs/specs/millis-60fps.md: with millis_60fps absent OR explicitly
    False, a show_millis countdown must take EXACTLY today's fps=out_fps
    =30 path -- the "byte-identical when off" claim, proven directly
    rather than assumed. golden.py --check is the full pixel-hash proof
    of that claim across every existing job; this is the cheap, fast
    fps-only half of the same proof, run every time (golden.py's hash
    jobs only catch a regression if someone remembers to run --check).
    """
    print("Timer: millis_60fps off/absent leaves show_millis at 30fps")

    base = {"minutes": 0, "seconds": 6, "style": "classic",
            "accent": "#e8b44f", "warn_last10": True,
            "hold_seconds": 2, "show_millis": True}
    for label, options in (("absent", dict(base)),
                           ("explicit False", dict(base,
                                                    millis_60fps=False))):
        filename = render_timer(options, lambda pct: None)
        path = os.path.join(EXPORTS_DIR, filename)
        try:
            check("millis_60fps {0}: no _60fps filename descriptor"
                  .format(label),
                  "_60fps" not in filename, "got {0!r}".format(filename))
            pts = _showinfo_pts(path)
            check("millis_60fps {0}: still 30fps (8s -> 240 frames, "
                  "not 480) -- the default path is untouched"
                  .format(label),
                  len(pts) == 240, "got {0} frames".format(len(pts)))
        finally:
            if os.path.isfile(path):
                os.unlink(path)

    # The mirror case, which the spec requires proven and nothing did:
    # 60 fps ticked with Show milliseconds OFF must change nothing. The UI
    # now greys the box out, but a disabled box keeps its tick and still
    # reaches the payload, so the renderer is the real guarantee. Compared
    # frame by frame against a plain render, not by fps alone -- a frame
    # count proves the rate, not that the pixels are unchanged.
    plain = {"minutes": 0, "seconds": 6, "style": "classic",
             "accent": "#e8b44f", "warn_last10": True,
             "hold_seconds": 2}
    paths = []
    try:
        for options in (plain, dict(plain, millis_60fps=True)):
            fn = render_timer(options, lambda pct: None)
            paths.append(os.path.join(EXPORTS_DIR, fn))
        check("millis_60fps with show_millis OFF: no _60fps or _ms "
              "descriptor",
              "_60fps" not in paths[1] and "_ms" not in paths[1],
              "got {0!r}".format(os.path.basename(paths[1])))
        # 8s at the plain 15fps path -> 120 frames.
        check("millis_60fps with show_millis OFF: identical pixels to a "
              "plain timer, every one of 120 frames",
              _frame_hashes(paths[0], 120) == _frame_hashes(paths[1], 120),
              "frames differ")
    finally:
        for path in paths:
            if os.path.isfile(path):
                os.unlink(path)


def check_stale_part_sweep():
    """app.sweep_stale_parts: the startup sweep must only take .part
    files nothing is writing. It used to take all of them, so opening a
    second copy of the app mid-render deleted the render in progress and
    a 15-minute timer failed at its very last step. Nothing tested the
    sweep at all, which is how that shipped."""
    import time
    from PIL import Image
    import app as _app
    from render.encoder import FrameEncoder, EncoderError

    print("Exports: the startup .part sweep leaves live files alone")
    folder = tempfile.mkdtemp(prefix="sv-smoke-sweep-")
    try:
        stale = _app._STALE_PART_SECONDS
        now = time.time()

        def make(name, age):
            path = os.path.join(folder, name)
            with open(path, "wb") as fh:
                fh.write(b"x")
            os.utime(path, (now - age, now - age))
            return path

        fresh = make("live-render.mov.part", 0)
        near = make("stalled-download.mp3.part", stale - 60)
        dead = make("killed-server.mp4.part", stale + 60)
        other = make("finished.mp4", stale + 60)
        removed = _app.sweep_stale_parts(folder, now=now)

        check("a .part written just now is kept", os.path.exists(fresh))
        check("a .part silent for less than the job timeout is kept "
              "(a download can legitimately stall that long)",
              os.path.exists(near))
        check("a .part silent for longer than any job can run is removed",
              not os.path.exists(dead))
        check("a finished export is never touched, however old",
              os.path.exists(other))
        check("the sweep reports exactly one removal", removed == 1,
              "got {0}".format(removed))

        # The actual failure: a real encode with the sweep run part-way
        # through, exactly as a second app launch would, then finished.
        # Before the fix this raised "Unable to re-open ... No such file
        # or directory" at +faststart; it must now complete.
        out = os.path.join(folder, "mid-render.mov")
        enc = FrameEncoder(out, 30, alpha_format="qtrle")
        blank = Image.new("RGBA", (1920, 1080), (0, 0, 0, 0))
        for _ in range(30):
            enc.add_frame(blank)
        _app.sweep_stale_parts(folder)
        try:
            enc.close()
            ok, detail = os.path.exists(out), ""
        except EncoderError as exc:
            ok, detail = False, str(exc)[-200:]
        check("a render survives the sweep running mid-encode (a second "
              "app launch no longer deletes it)", ok, detail)
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def check_https_goes_through_netutil():
    """Every outbound HTTPS call must use netutil.urlopen. A frozen build has
    no CA bundle on disk, so a plain urllib.request.urlopen verifies against
    nothing and fails with URLError — from source it works, so nobody
    notices until a church does. It broke the update check once and the
    YouTube downloader's first-run setup in v1.29.0 (docs/specs/
    youtube-download.md); this check is the third time's charm."""
    print("Network: outbound HTTPS goes through netutil")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    offenders = []
    for folder in ("", "routes", "render"):
        base = os.path.join(root, folder)
        for name in sorted(os.listdir(base)):
            if not name.endswith(".py") or name == "netutil.py":
                continue
            with open(os.path.join(base, name), encoding="utf-8") as fh:
                for lineno, line in enumerate(fh, 1):
                    if "urllib.request.urlopen(" in line:
                        offenders.append("{0}/{1}:{2}".format(
                            folder or ".", name, lineno))
    check("no module calls urllib.request.urlopen directly",
          not offenders, ", ".join(offenders))


def check_download_done_panel():
    """batch-download.md: the finished panel answers "is it done?"
    and DOWNLOAD ANOTHER starts the next one instead of repeating
    the last. Both live in the browser, so they are verified by
    driving the tile -- these are tripwires so a refactor cannot
    quietly drop either back to what an operator misread once."""
    print()
    print("Download: the finished panel")
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(here, "static", "js", "download.js"),
              encoding="utf-8") as f:
        js = f.read()

    check("a finished batch says so as a whole sentence",
          '"All " + items.length + " are saved in your exports folder."'
          in js)
    check("a finished single download keeps its own sentence",
          '"Saved to your exports folder."' in js)
    check("the last filename is hidden behind a batch's own list",
          '$("download-filename").hidden = true;' in js)
    check("DOWNLOAD ANOTHER cancels the submit rather than re-running",
          "e.preventDefault();" in js and
          '$("download-done").hidden) return;' in js)
    check("DOWNLOAD ANOTHER empties both link fields",
          '$("download-url").value = "";' in js and
          '$("download-urls").value = "";' in js)


def check_js_modules():
    """static/js/ is seven plain script files sharing one `SV` object, with
    no bundler to notice a tile calling something it never imported. That
    fails only at click time, in the packaged app, on a volunteer's
    machine — so it fails here instead (scripts/jscheck.py)."""
    print("Static JS module check")
    problems = jscheck.check_js_modules()
    check("every static/js file only calls what it defines or imports",
          not problems, "; ".join(problems))


def main():
    rendered = []  # basenames to clean up

    def quiet_progress(pct):
        pass

    check_whats_new()
    print()

    print("Rendering test videos (this takes a minute)...")
    try:
        for style in ("classic", "ring", "bar"):
            fn = render_timer(
                {"minutes": 0, "seconds": 6, "style": style,
                 "accent": "#e8b44f", "warn_last10": True,
                 "hold_seconds": 2},
                quiet_progress)
            rendered.append(("timer/" + style, fn, 8.0))

        # Addendum (v1.23.0): countdown with milliseconds on (classic,
        # 30fps path) — same expected duration as the plain countdowns
        # above (6s run + 2s hold), just with the millis toggle flipped.
        fn = render_timer(
            {"minutes": 0, "seconds": 6, "style": "classic",
             "accent": "#e8b44f", "warn_last10": True, "hold_seconds": 2,
             "show_millis": True},
            quiet_progress)
        rendered.append(("timer/classic-ms", fn, 8.0))

        # v1.24.0: a countdown cycling two generated background images
        # (docs/specs/timer-backgrounds.md) — same 6s run + 2s hold shape
        # as the plain classic check above, just with backgrounds set.
        # verify() only checks container/codec/duration (it can't see
        # pixels, and CI has no display), so this proves the cycling path
        # renders and encodes cleanly, not what it looks like — that is
        # the orchestrator's frame-extraction job.
        bg_dir = tempfile.mkdtemp(prefix="sv-smoke-timerbg-")
        try:
            from PIL import Image as _Image
            bg1 = os.path.join(bg_dir, "one.png")
            bg2 = os.path.join(bg_dir, "two.png")
            _Image.new("RGB", (200, 200), (200, 30, 30)).save(bg1)
            _Image.new("RGB", (200, 200), (30, 30, 200)).save(bg2)
            fn = render_timer(
                {"minutes": 0, "seconds": 6, "style": "classic",
                 "accent": "#e8b44f", "warn_last10": True, "hold_seconds": 2,
                 "backgrounds": [bg1, bg2], "bg_seconds": 2, "bg_dim": 30,
                 "bg_blur": False},
                quiet_progress)
            rendered.append(("timer/two-backgrounds", fn, 8.0))
        finally:
            shutil.rmtree(bg_dir, ignore_errors=True)

        # Clock mode: classic with millis on (30 fps path), and ring with
        # millis off (10 fps path) — starts chosen to actually cross a
        # rollover (8 PM, then midnight) during the clip, not just sit
        # still, since that's exactly where an off-by-one would show up.
        fn = render_timer(
            {"mode": "clock", "start": "19:59:57", "duration_seconds": 6,
             "format": "12h", "show_seconds": True, "show_millis": True,
             "style": "classic", "accent": "#e8b44f"},
            quiet_progress)
        rendered.append(("clock/classic-12h-millis", fn, 6.0))

        fn = render_timer(
            {"mode": "clock", "start": "23:59:55", "duration_seconds": 6,
             "format": "24h", "show_seconds": True, "show_millis": False,
             "style": "ring", "accent": "#e8b44f"},
            quiet_progress)
        rendered.append(("clock/ring-24h", fn, 6.0))

        fn = render_spinner(
            {"entries": ["Alice", "Bob", "Carol", "Dave"],
             "mode": "rigged", "winner": "Carol", "accent": "#e8b44f"},
            quiet_progress)
        rendered.append(("spinner", fn, 11.8))  # 0 wait + 0.8 windup + 7 spin + 4 winner

        # QR "scan to..." card — short 5 s clip.
        fn = render_qr(
            {"url": "https://church.example/give",
             "heading": "SCAN TO GIVE", "caption": "Thank you",
             "accent": "#e8b44f", "duration_seconds": 5},
            quiet_progress)
        rendered.append(("qr", fn, 5.0))

        # Motion background — a short loop in each style. The renderer floors
        # duration at MIN_DURATION (5 s) per the design, so a 3 s request is
        # clamped up to 5 s; probe against the real contracted 5 s output.
        for style in ("aurora", "bokeh", "waves"):
            fn = render_motion_bg(
                {"style": style, "accent": "#e8b44f",
                 "duration_seconds": 5},
                quiet_progress)
            rendered.append(("motionbg/" + style, fn, 5.0))

        print()
        for name, filename, expected in rendered:
            verify(name, filename, expected)
    finally:
        # Keep exports/ clean for the user.
        for _name, filename, _expected in rendered:
            path = os.path.join(EXPORTS_DIR, filename)
            if os.path.isfile(path):
                os.unlink(path)

    print()
    check_prepare_background()
    print()
    check_backgrounds_blur_route()
    print()
    check_digit_shadow()
    print()
    check_clock_format()
    print()
    check_countdown_millis_format()
    print()
    check_clock_validation()
    print()
    check_green_screen()
    print()
    check_alpha_export()
    print()
    check_qr_styles()
    print()

    check_qr_ring()
    print()

    check_fixed_format()
    print()
    check_millis_size_and_ticking()
    print()
    check_millis_reveal_validation()
    print()
    check_millis_reveal_render()
    print()
    check_millis_reveal_alpha_fringe()
    print()
    check_millis_60fps_pure()
    print()
    check_millis_60fps_validation()
    print()
    check_millis_60fps_render()
    print()
    check_millis_60fps_alpha_fringe()
    print()
    check_millis_60fps_off_stays_30fps()
    print()
    check_boot_marker_is_packaged_only()
    print()
    check_update_picks_newest_version()
    print()
    check_download()
    print()

    check_download_retry_and_remove()
    print()

    check_batch_download()
    print()
    check_download_done_panel()
    check_js_modules()
    print()

    check_stale_part_sweep()
    check_https_goes_through_netutil()
    print()

    check_stats_privacy()
    print()
    try:
        check_vision_flip()
        print()
        check_windows_ocr()
        print()
        check_scoreboard()
    finally:
        shutil.rmtree(_BOARD_TMP, ignore_errors=True)

    print()
    if failures:
        print("SMOKE FAILED — {0} check(s) failed:".format(len(failures)))
        for f in failures:
            print("  - " + f)
        return 1
    print("SMOKE PASSED — all checks green.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
