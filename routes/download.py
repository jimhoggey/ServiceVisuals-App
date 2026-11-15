"""/api/download/status — the YouTube-download tile's one extra endpoint
(docs/specs/youtube-download.md).

Split out like routes/backgrounds.py. The actual render (type="download")
goes through the same /api/render + JobManager path every other tile
uses (wired in app.py) — this blueprint only answers the status check the
tile makes on entering its view, before the operator has clicked anything.
"""

import os

from flask import Blueprint, abort, jsonify, send_file

import tools
from render import trim

bp = Blueprint("download", __name__)


@bp.route("/api/download/status", methods=["GET"])
def api_download_status():
    return jsonify(tools.tools_status())


@bp.route("/api/download/tools", methods=["DELETE"])
def api_download_tools_remove():
    """The REMOVE button beside the status line: the two fetched binaries
    are ~120 MB on disk, and a church that tried the tile once should be
    able to give that back without finding ~/.service-visuals by hand."""
    return jsonify(tools.remove_tools())


@bp.route("/api/download/media/<path:filename>", methods=["GET"])
def api_download_media(filename):
    """The trim panel's player (docs/specs/clip-trim.md). Inline, not an
    attachment, and conditional so range requests work -- without them
    the player cannot seek, and SET START HERE would be useless on a
    15-minute video."""
    if os.path.splitext(filename)[1].lower() not in trim.TRIM_EXTS:
        abort(404)
    path = trim.resolve_export(filename)
    if path is None:
        abort(404)
    mimetype = "audio/mpeg" if path.lower().endswith(".mp3") else "video/mp4"
    return send_file(path, mimetype=mimetype, conditional=True)
