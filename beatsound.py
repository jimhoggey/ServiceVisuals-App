"""The beat opener's own sound (docs/specs/beat-opener.md, "Your own
sound"): one short clip the band records, stored once and played on every
white-to-black cut instead of the built-in tone.

Decoding goes through the bundled ffmpeg, so WAV, MP3 and M4A all work
with nothing new installed. The stored copy is already trimmed and
normalised, so a render only has to place it.

No numpy (it does not ship in the app): the note detection is a plain
autocorrelation on half a second of audio, decimated to keep it to a
fraction of a second.
"""

import array
import json
import math
import os
import subprocess
import sys
import wave

import imageio_ffmpeg

from render.beat import replace_with_retry, unlink_quietly

SOUND_DIR = os.path.join(
    os.environ.get("SERVICE_VISUALS_CONFIG") or
    os.path.join(os.path.expanduser("~"), ".service-visuals"),
    "beat-sound")
# The whole uploaded sound (waveform trimmer, docs/specs/beat-opener.md):
# kept untrimmed so the volunteer can pick any part of it later.
ORIGINAL_PATH = os.path.join(SOUND_DIR, "original.wav")
# v1.42.0 stored only the trimmed clip, here. Still read when no
# original.wav exists, so a sound uploaded before the trimmer keeps
# working; never written any more.
SOUND_PATH = os.path.join(SOUND_DIR, "sound.wav")
META_PATH = os.path.join(SOUND_DIR, "sound.json")

SAMPLE_RATE = 44100
MAX_INPUT_SECONDS = 30      # decode no more than this of whatever arrives
PEAK = 0.89
# The selection. 2.0 s is two beats at the slowest BPM (60): the window is
# capped at the cut, and the UI caps it further at the current BPM.
MIN_LENGTH = 0.05
MAX_LENGTH = 2.0
_FADE_IN = 0.003            # a window starting mid-sound must not click
_FADE_OUT = 0.03

ERR_NOT_NUMBERS = "Start and length must be numbers."
ERR_LENGTH = "Choose between 0.05 and 2 seconds of your sound."
ERR_OUTSIDE = "That part is outside your sound."
ERR_NO_SOUND = "Upload your sound first."

NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")

ERR_NONE = "No sound was uploaded."
ERR_UNREADABLE = ("That file isn't a sound the app can read — use "
                  "WAV, MP3 or M4A.")
ERR_SILENT = "That sound is silent — record it again a little louder."


class SoundError(Exception):
    """A plain-English message for the volunteer."""


def _no_window():
    if sys.platform == "win32":
        return {"creationflags": 0x08000000}       # CREATE_NO_WINDOW
    return {}


def decode(path):
    """Interleaved stereo 16-bit samples (array 'h') of the first
    MAX_INPUT_SECONDS of `path`, at SAMPLE_RATE. Raises SoundError."""
    result = subprocess.run(
        [imageio_ffmpeg.get_ffmpeg_exe(), "-nostdin", "-v", "error",
         "-i", path,
         "-t", str(MAX_INPUT_SECONDS), "-vn", "-ac", "2",
         "-ar", str(SAMPLE_RATE), "-f", "s16le", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, **_no_window())
    if result.returncode != 0 or len(result.stdout) < 4:
        raise SoundError(ERR_UNREADABLE)
    pcm = array.array("h")
    pcm.frombytes(result.stdout[:len(result.stdout) // 4 * 4])
    if sys.byteorder != "little":
        pcm.byteswap()           # ffmpeg was asked for little-endian
    return pcm


def normalise(pcm):
    """The whole sound with its peak set to PEAK. Raises SoundError when
    there is nothing to hear."""
    peak = max(abs(v) for v in pcm) if pcm else 0
    if peak < 33:                                  # about -60 dBFS
        raise SoundError(ERR_SILENT)
    gain = PEAK * 32767.0 / peak
    return array.array("h", (int(v * gain) for v in pcm))


def attack_start(pcm):
    """Seconds to the first real sound, less a 5 ms pre-roll: where the
    selection starts by default. This is what makes the hit land ON the
    cut -- a recording usually starts with a breath of silence, and 100 ms
    of it would put every hit audibly late."""
    peak = max(abs(v) for v in pcm) if pcm else 0
    threshold = 0.05 * peak
    first = next((i for i, v in enumerate(pcm) if abs(v) >= threshold), 0)
    return max(0, first // 2 - int(0.005 * SAMPLE_RATE)) / float(SAMPLE_RATE)


def _region(pcm, start, length):
    """Interleaved samples from `start` for `length` seconds."""
    first = int(round(start * SAMPLE_RATE)) * 2
    count = int(round(length * SAMPLE_RATE)) * 2
    return pcm[first:first + count]


def detect_note(pcm, sample_rate=SAMPLE_RATE):
    """The clip's note name, or None when no single note clearly leads.

    Energy on each of the 12 notes, summed over octaves 2-6 (Goertzel
    filters on up to 0.8 s from the attack, mono, decimated 4x). Only the
    NAME is wanted, so octaves pooling together is the point, not a flaw.
    This replaced an autocorrelation detector that called the owner's own
    reference clip -- a G# pad under crowd noise -- "couldn't tell"; the
    energy method names it. The winner must carry at least twice the
    runner-up's energy... was the first rule, and it rejected that clip
    (G# led F# by only 1.24x -- the band underneath) while white noise
    "led" by 1.34x. So two tests instead: the winner leads the runner-up
    by 1.15x, AND it is tonal -- its energy sits ON the note, at least
    2x the energy a quarter-tone either side (measured: that clip 2.24x,
    white noise 1.0-1.4x, a clean tone in the hundreds). Noise is flat,
    so it fails the second; a chord's notes tie, so it fails the first.
    A confident wrong note would be worse than "couldn't tell".
    """
    step = 4
    rate = sample_rate / float(step)
    x = [(pcm[i] + pcm[i + 1]) / 2.0
         for i in range(0, min(len(pcm), int(0.8 * sample_rate) * 2),
                        2 * step)]
    n = len(x)
    if n < int(rate * 0.1):
        return None
    # A Hann window, so a strong note doesn't leak into its neighbours.
    x = [v * (0.5 - 0.5 * math.cos(2 * math.pi * i / (n - 1)))
         for i, v in enumerate(x)]
    def power(freq):
        coeff = 2 * math.cos(2 * math.pi * freq / rate)
        s1 = s2 = 0.0
        for v in x:
            s1, s2 = v + coeff * s1 - s2, s1
        return s1 * s1 + s2 * s2 - coeff * s1 * s2

    chroma = [0.0] * 12
    for midi in range(36, 96):                  # C2 .. B6
        chroma[midi % 12] += power(440.0 * 2 ** ((midi - 69) / 12.0))
    order = sorted(range(12), key=lambda k: chroma[k], reverse=True)
    top = chroma[order[0]]
    if top <= 0 or top < 1.15 * chroma[order[1]]:
        return None
    between = 0.0
    for midi in range(36 + order[0] % 12, 96, 12):
        for off in (-0.5, 0.5):
            between += power(440.0 * 2 ** ((midi + off - 69) / 12.0))
    if top < 2 * (between / 2.0):
        return None
    return NOTE_NAMES[order[0]]


def _write_wav(path, pcm):
    tmp = path + ".part"
    out = array.array("h", pcm)
    if sys.byteorder != "little":
        out.byteswap()
    with wave.open(tmp, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(out.tobytes())
    replace_with_retry(tmp, path)


def _read_wav(path):
    with wave.open(path) as w:
        pcm = array.array("h")
        pcm.frombytes(w.readframes(w.getnframes()))
    if sys.byteorder != "little":
        pcm.byteswap()
    return pcm


def audio_path():
    """The whole stored sound -- original.wav, or a v1.42.0 sound.wav --
    or None."""
    for path in (ORIGINAL_PATH, SOUND_PATH):
        if os.path.isfile(path):
            return path
    return None


def _duration(path):
    with wave.open(path) as w:
        return w.getnframes() / float(w.getframerate())


def _write_meta(start, length, note):
    with open(META_PATH, "w") as f:
        json.dump({"start": start, "length": length, "note": note}, f)


def save_upload(stream):
    """Store an uploaded file-like `stream` as THE beat sound, whole, with
    the selection starting at its attack. Returns the status() shape plus
    "trimmed" (the input ran past MAX_INPUT_SECONDS); raises SoundError."""
    os.makedirs(SOUND_DIR, exist_ok=True)
    incoming = os.path.join(SOUND_DIR, "upload.part")
    try:
        with open(incoming, "wb") as f:
            while True:
                chunk = stream.read(1 << 16)
                if not chunk:
                    break
                f.write(chunk)
        pcm = normalise(decode(incoming))
    finally:
        # A lock (Defender scanning the fresh file) must not turn a good
        # upload -- or a plain-English SoundError -- into a 500. A
        # leftover upload.part is harmless: the next upload overwrites it.
        unlink_quietly(incoming)
    duration = len(pcm) / 2.0 / SAMPLE_RATE
    start = round(attack_start(pcm), 3)
    length = round(max(MIN_LENGTH, min(MAX_LENGTH, duration - start)), 3)
    _write_wav(ORIGINAL_PATH, pcm)
    unlink_quietly(SOUND_PATH)        # a v1.42.0 clip no longer applies
    _write_meta(start, length, detect_note(_region(pcm, start, length)))
    result = status()
    result["trimmed"] = duration >= MAX_INPUT_SECONDS - 0.01
    return result


def set_selection(start, length):
    """Store a new selection; the note is re-detected on that part.
    Returns the status() shape; raises SoundError."""
    path = audio_path()
    if path is None:
        raise SoundError(ERR_NO_SOUND)
    numbers = all(isinstance(v, (int, float)) and not isinstance(v, bool)
                  and v == v for v in (start, length))   # v == v: not NaN
    if not numbers:
        raise SoundError(ERR_NOT_NUMBERS)
    if not MIN_LENGTH - 1e-9 <= length <= MAX_LENGTH + 1e-9:
        raise SoundError(ERR_LENGTH)
    duration = _duration(path)
    if start < 0 or start + length > duration + 0.002:
        raise SoundError(ERR_OUTSIDE)
    start, length = round(start, 3), round(length, 3)
    pcm = _read_wav(path)
    _write_meta(start, length, detect_note(_region(pcm, start, length)))
    return status()


def status():
    """{"present": False}, or {"present", "note", "seconds" (the
    selection's length), "start", "duration" (the whole sound's)}."""
    path = audio_path()
    if path is None:
        return {"present": False}
    try:
        with open(META_PATH) as f:
            meta = json.load(f)
    except (OSError, ValueError):
        meta = {}
    duration = round(_duration(path), 3)
    start = meta.get("start")
    length = meta.get("length")
    if not isinstance(start, (int, float)) or \
            not isinstance(length, (int, float)):
        # A v1.42.0 sound: its clip is already trimmed to the attack.
        start, length = 0.0, round(min(MAX_LENGTH, duration), 3)
    note = meta.get("note") if meta.get("note") in NOTE_NAMES else None
    return {"present": True, "note": note, "seconds": length,
            "start": start, "duration": duration}


def remove():
    for path in (ORIGINAL_PATH, SOUND_PATH, META_PATH):
        unlink_quietly(path)
    return {"present": False}


def load():
    """The selected part as interleaved stereo samples (array 'h'), with
    a 3 ms fade-in and a 30 ms fade-out, or None. The render caps it at
    the next cut as well."""
    path = audio_path()
    if path is None:
        return None
    info = status()
    clip = _region(_read_wav(path), info["start"], info["seconds"])
    frames = len(clip) // 2
    fade_in = int(_FADE_IN * SAMPLE_RATE)
    fade_out = int(_FADE_OUT * SAMPLE_RATE)
    out = array.array("h", clip)
    for j in range(frames):
        g = 1.0
        if j < fade_in:
            g = j / float(fade_in)
        if j >= frames - fade_out:
            g = min(g, (frames - j) / float(fade_out))
        if g < 1.0:
            out[2 * j] = int(clip[2 * j] * g)
            out[2 * j + 1] = int(clip[2 * j + 1] * g)
    return out
