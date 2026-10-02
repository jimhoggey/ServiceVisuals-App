"""/api/beat-sound — the beat opener's own sound (docs/specs/beat-opener.md,
"Your own sound"). Synchronous like /api/backgrounds: decoding a few
seconds of audio takes well under a second, so none of this goes near
the render queue."""

from flask import Blueprint, jsonify, request, send_file

import beatsound

bp = Blueprint("beatsound", __name__)


@bp.route("/api/beat-sound", methods=["GET"])
def api_beat_sound_status():
    return jsonify(beatsound.status())


@bp.route("/api/beat-sound/audio", methods=["GET"])
def api_beat_sound_audio():
    """The whole stored sound, for the waveform and the ▶ preview -- the
    browser decodes it itself, so there is no waveform code here."""
    path = beatsound.audio_path()
    if path is None:
        return jsonify({"error": beatsound.ERR_NO_SOUND}), 404
    return send_file(path, mimetype="audio/wav", max_age=0)


@bp.route("/api/beat-sound", methods=["PUT"])
def api_beat_sound_select():
    """{"start", "length"} in seconds -> the status, note re-detected."""
    data = request.get_json(silent=True) or {}
    try:
        return jsonify(beatsound.set_selection(data.get("start"),
                                               data.get("length")))
    except beatsound.SoundError as exc:
        return jsonify({"error": str(exc)}), 400


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
