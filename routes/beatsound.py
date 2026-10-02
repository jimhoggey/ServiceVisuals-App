"""/api/beat-sound — the beat opener's own sound (docs/specs/beat-opener.md,
"Your own sound"). Synchronous like /api/backgrounds: decoding a few
seconds of audio takes well under a second, so none of this goes near
the render queue."""

from flask import Blueprint, jsonify, request

import beatsound

bp = Blueprint("beatsound", __name__)


@bp.route("/api/beat-sound", methods=["GET"])
def api_beat_sound_status():
    return jsonify(beatsound.status())


@bp.route("/api/beat-sound", methods=["POST"])
def api_beat_sound_upload():
    file = request.files.get("sound")
    if file is None or not file.filename:
        return jsonify({"error": beatsound.ERR_NONE}), 400
    try:
        return jsonify(beatsound.save_upload(file.stream))
    except beatsound.SoundError as exc:
        return jsonify({"error": str(exc)}), 400


@bp.route("/api/beat-sound", methods=["DELETE"])
def api_beat_sound_remove():
    return jsonify(beatsound.remove())
