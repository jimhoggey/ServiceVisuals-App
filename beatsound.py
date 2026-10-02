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
SOUND_PATH = os.path.join(SOUND_DIR, "sound.wav")
META_PATH = os.path.join(SOUND_DIR, "sound.json")

SAMPLE_RATE = 44100
MAX_INPUT_SECONDS = 30      # decode no more than this of whatever arrives
KEEP_SECONDS = 4.0          # what is kept after the attack
PEAK = 0.89

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


def trim_and_normalise(pcm):
    """(samples, trimmed): the attack moved to the very start, the first
    KEEP_SECONDS kept, peak set to PEAK. The trim is what makes the hit
    land ON the cut: a recording usually starts with a breath of silence,
    and 100 ms of it would put every hit audibly late."""
    peak = max(abs(v) for v in pcm) if pcm else 0
    if peak < 33:                                  # about -60 dBFS
        raise SoundError(ERR_SILENT)
    threshold = 0.05 * peak
    first = next(i for i, v in enumerate(pcm) if abs(v) >= threshold)
    first = max(0, first // 2 - int(0.005 * SAMPLE_RATE)) * 2
    keep = int(KEEP_SECONDS * SAMPLE_RATE) * 2
    trimmed = len(pcm) - first > keep
    clip = pcm[first:first + keep]
    gain = PEAK * 32767.0 / peak
    return array.array("h", (int(v * gain) for v in clip)), trimmed


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


def save_upload(stream):
    """Store an uploaded file-like `stream` as THE beat sound. Returns
    {"present", "note", "seconds", "trimmed"}; raises SoundError."""
    os.makedirs(SOUND_DIR, exist_ok=True)
    incoming = os.path.join(SOUND_DIR, "upload.part")
    try:
        with open(incoming, "wb") as f:
            while True:
                chunk = stream.read(1 << 16)
                if not chunk:
                    break
                f.write(chunk)
        samples, trimmed = trim_and_normalise(decode(incoming))
    finally:
        # A lock (Defender scanning the fresh file) must not turn a good
        # upload -- or a plain-English SoundError -- into a 500. A
        # leftover upload.part is harmless: the next upload overwrites it.
        unlink_quietly(incoming)
    note = detect_note(samples)
    seconds = round(len(samples) / 2.0 / SAMPLE_RATE, 2)
    tmp = SOUND_PATH + ".part"
    out = array.array("h", samples)
    if sys.byteorder != "little":
        out.byteswap()
    with wave.open(tmp, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(out.tobytes())
    replace_with_retry(tmp, SOUND_PATH)
    with open(META_PATH, "w") as f:
        json.dump({"note": note, "seconds": seconds}, f)
    return {"present": True, "note": note, "seconds": seconds,
            "trimmed": trimmed}


def status():
    """{"present": False} or {"present", "note", "seconds"}."""
    if not os.path.isfile(SOUND_PATH):
        return {"present": False}
    try:
        with open(META_PATH) as f:
            meta = json.load(f)
    except (OSError, ValueError):
        meta = {}
    note = meta.get("note") if meta.get("note") in NOTE_NAMES else None
    seconds = meta.get("seconds")
    return {"present": True, "note": note,
            "seconds": seconds if isinstance(seconds, (int, float)) else None}


def remove():
    for path in (SOUND_PATH, META_PATH):
        if os.path.exists(path):
            os.unlink(path)
    return {"present": False}


def load():
    """The stored clip as interleaved stereo samples (array 'h'), or None."""
    if not os.path.isfile(SOUND_PATH):
        return None
    with wave.open(SOUND_PATH) as w:
        pcm = array.array("h")
        pcm.frombytes(w.readframes(w.getnframes()))
    if sys.byteorder != "little":
        pcm.byteswap()
    return pcm
