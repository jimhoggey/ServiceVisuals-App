"""Timer background image library (docs/specs/timer-backgrounds.md).

Split out of app.py (docs/specs/refactor-organise.md): the directory, the
id shape, the two library-size limits, and the small id-validating /
path-resolving / PNG-serving helpers every backgrounds route (and
validation._backgrounds_field) needs. No import of app — routes/backgrounds
.py owns the actual /api/backgrounds/* routes and imports this module.
"""

import io
import os
import re

from flask import send_file

# Timer background images, kept across restarts under ~/.service-visuals
# like boards and the API key — never inside the app bundle. Honours
# SERVICE_VISUALS_CONFIG so smoke.py's throwaway config dir isolates this
# too (same convention as render.scoreboard's BOARDS_DIR).
BACKGROUNDS_DIR = os.path.join(
    os.environ.get("SERVICE_VISUALS_CONFIG") or
    os.path.join(os.path.expanduser("~"), ".service-visuals"),
    "backgrounds")

# 16 lowercase hex chars, minted by uuid4().hex[:16] — used as a FILENAME
# (id + ".png"), so it is checked against this before anything touches the
# filesystem, same discipline as BOARD_ID_RE.
BACKGROUND_ID_RE = re.compile(r"[a-f0-9]{16}")
BACKGROUND_LIBRARY_MAX = 40

# One timer may use everything in the library — there is no extra limit on
# top of it. It used to be 10, which arrived with the feature (v1.24.0)
# and was never justified anywhere; the owner asked for it gone unless it
# earned its place. What it costs, measured: render/timer.py builds every
# background into a full 1920x1080 plate and holds ALL of them for the
# whole render, about 8 MB each — 1 image +24 MB, 10 +96 MB, 20 +175 MB,
# 40 +334 MB of resident memory on top of a ~34 MB baseline. So the real
# ceiling is worth keeping, and the library's own 40 is it. Raising THAT
# means making the plates lazy first (render/timer.py _plates), not just
# changing this number.
BACKGROUNDS_PER_TIMER_MAX = BACKGROUND_LIBRARY_MAX


def _send_png(path):
    """Serve a PNG without keeping the file open.

    send_file(path) hands Flask the path and the handle stays open until
    the response is finalised. POSIX does not care — you can unlink an
    open file — but Windows refuses, so deleting a background image the
    preview had just displayed raised straight out of os.unlink and the
    route 500'd. CI caught it on the Windows runner and nowhere else.
    Reading the bytes first costs one copy of a ~2 MB image over
    localhost and makes delete behave the same on every platform.
    """
    with open(path, "rb") as handle:
        return send_file(io.BytesIO(handle.read()), mimetype="image/png")


def _background_path(image_id, suffix=".png"):
    """Resolve a validated id to its file, with the same realpath
    containment check every other id-addressed route in this file uses
    (_background_field, /api/board/<id>/source.png). `suffix` also
    addresses the cached ?blur=1 variant (<id>.blur.png) alongside the
    original — same containment check, just a different filename."""
    root = os.path.realpath(BACKGROUNDS_DIR)
    path = os.path.realpath(os.path.join(root, image_id + suffix))
    if not path.startswith(root + os.sep):
        return None
    return path
