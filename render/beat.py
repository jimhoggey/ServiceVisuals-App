"""Beat opener (docs/specs/beat-opener.md): the beat grid, the tone, and
the pass that puts the tone into the finished MP4.

A conference-opener countdown flashes the screen white and black on the
song's beat and sounds the song's key, so the band comes in at 0:00 in time
and in tune. Everything here is pure or a single ffmpeg call, so smoke can
exercise the grid and the audio without rendering a frame.

The grid is anchored at 0:00, not at the start of the video: a 20-second
timer at 125 BPM is 41.67 beats, and the band counts in from the screen,
so it is zero that must land on a beat. The start may cut into a beat.

The tone is a pad, not a ping. The first version was a bell that died
after 0.9 s, and the owner called it monotone and dry; the reference clip
turned out to hold a sustained pad that swells on every cut and is never
silent. It is the SAME swell on every cut -- the owner ruled out any build
toward zero ("that screams AI").

No numpy and no audio library: numpy does not ship in the app, and the
library that was considered (pedalboard) needs Python 3.10+ and is GPL,
which would bind the whole bundled app. The pad is plain Python over a
seamless 4-second loop; the reverb is ffmpeg's own convolution filter
(afir) with an impulse response generated here, run as a separate program
exactly like every other ffmpeg call in the app.
"""

import array
import math
import os
import random
import subprocess
import sys
import time
import wave

import imageio_ffmpeg

BPM_MIN = 60
BPM_MAX = 200
BPM_DEFAULT = 120
KEYS = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
KEY_DEFAULT = "A"
MAX_SECONDS = 900          # the 60 fps ceiling (docs/specs/millis-60fps.md)

SAMPLE_RATE = 44100
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
INK = (17, 17, 17)         # digits on a white beat

# The reference clip's tone was one note in three octaves with the middle
# one strongest (415 / 830 / 1661 Hz for G#). Octave 3 adds warmth under
# it. Octaves only -- no fifth or third, so it is "just the key", and
# major or minor can't clash.
_OCTAVES = ((3, 0.30), (4, 0.65), (5, 1.0), (6, 0.22))
# Each voice is a soft, rounded wave (fundamental + a little 2nd and 3rd
# harmonic): warmer than a sine, nowhere near a buzzy saw.
_HARMONICS = ((1, 1.0), (2, 0.22), (3, 0.07))
# No detuned voices. They were tried: their slow beating made the pad's
# level wander, so one cut landed loud and the next soft (measured 65 %
# apart) -- and the owner asked for the same tone on every cut. Width
# comes from giving each octave a different phase in each ear instead,
# which is steady, plus the stereo reverb.
_RIGHT_PHASE = {3: 0.25, 4: 0.5, 5: 0.75, 6: 0.125}   # cycles
_LOOP = 4.0               # seconds; every voice is tuned to repeat in it
_TABLE = 4096

# A soft bell on every cut, in the key's upper octaves, over the swell:
# it gives each cut a defined start the pad's 35 ms swell alone lacks.
_BELL = ((5, 1.0), (6, 0.45), (7, 0.12))
_BELL_TAU = 0.14
_BELL_LEVEL = 0.35        # against the pad's full swell

# The owner heard v2 (floor 0.35, tau 0.40, a 2.2 s reverb) smear each
# cut into the next. Lower floor and a faster settle keep every hit its
# own; still never fully silent while counting.
_FLOOR = 0.18             # the sustain between cuts: never silent
_ATTACK = 0.035           # a swell, not a click
_TAU = 0.22               # how fast a swell settles back to the floor
_FADE_IN = 0.4            # the video may start mid-beat
_RELEASE = 0.8            # after 0:00 the tone rings out, then silence
_PEAK = 0.5               # dry peak; the reverb and limiter sit on top

# A short room, not a hall: at 125 BPM a cut comes every 0.96 s, and the
# first 2.2 s tail (decay 0.55 s) was still loud when the next cut hit.
_REVERB_SECONDS = 0.9
_REVERB_DECAY = 0.16      # seconds for the tail to fall ~63 %
_REVERB_MIX = 0.30        # wet level against the dry pad


def beat_index(i, fps, total, bpm):
    """Whole beats from 0:00 to the MIDDLE of frame `i` (negative before).

    Judged at mid-frame, so a flash edge lands on the frame nearest its
    beat -- at most half a frame (8 ms at 60 fps) early or late -- rather
    than always up to a whole frame late, which measured 13 ms behind the
    ping. 0:00 itself stays exact: frame total*fps is past it either way.

    Integer floor division on purpose: a float comparison could put a
    frame that sits exactly on a beat edge on either side of it, and the
    preview has to agree with the renderer on every frame.
    """
    return ((2 * i + 1 - 2 * total * fps) * bpm) // (120 * fps)


def screen_is_white(i, fps, total, bpm):
    """White on the odd beats ("two"), black on the even ones ("one",
    where the ping is) and from 0:00 on. The beat before zero is always
    -1, a white "two", so 0:00 itself is always a white-to-black cut."""
    if i >= total * fps:
        return False
    return beat_index(i, fps, total, bpm) % 2 == 1


def ping_times(total, bpm):
    """Start times of every ping, in seconds, earliest first: 0:00 and
    every second beat before it, back to the start of the video."""
    two_beats = 120.0 / bpm
    times = []
    m = 0
    while True:
        t = total - m * two_beats
        if t < -1e-9:
            break
        times.append(max(0.0, t))
        m += 1
    return times[::-1]


def key_frequency(key, octave=4):
    """Equal-tempered frequency of `key` in `octave` (A4 = 440 Hz)."""
    semitones = KEYS.index(key) - KEYS.index("A") + 12 * (octave - 4)
    return 440.0 * 2.0 ** (semitones / 12.0)


def voice_frequencies(key, octaves=_OCTAVES):
    """(octave, frequency, level) for every voice in `octaves`.

    Each frequency is rounded to a multiple of 1/_LOOP Hz, so every voice
    completes a whole number of cycles in _LOOP seconds and the loop
    repeats without a seam. The rounding is under 2 cents at octave 3 and
    shrinks above it -- inaudible, and A stays exactly 220 / 440 / 880."""
    step = 1.0 / _LOOP
    return [(octave, round(key_frequency(key, octave) / step) * step, level)
            for octave, level in octaves]


def _loop(key, octaves, harmonics, sample_rate):
    """_LOOP seconds of `octaves` at full level, seamless, as (left,
    right). Octaves only, so the level is steady wherever a cut lands.

    The bell is built here too, from the SAME phase grid as the pad: its
    880 Hz sits on the pad's 880 Hz, and when the bell was an independent
    sine the two met at a different phase on every cut -- reinforcing on
    some, half-cancelling on others (cuts measured 55 % apart)."""
    table = [sum(a * math.sin(2.0 * math.pi * h * k / _TABLE)
                 for h, a in harmonics) for k in range(_TABLE)]
    n = int(_LOOP * sample_rate)
    left = [0.0] * n
    right = [0.0] * n
    for octave, freq, level in voice_frequencies(key, octaves):
        # Whole cycles per loop, so the phase index is exact integer math
        # and sample n wraps back to sample 0 without a click.
        cycles = int(round(freq * _LOOP))
        tab = [level * v for v in table]
        shift = int(_RIGHT_PHASE.get(octave, 0.0) * _TABLE)
        left = [o + tab[(k * cycles * _TABLE // n) % _TABLE]
                for k, o in enumerate(left)]
        right = [o + tab[(k * cycles * _TABLE // n + shift) % _TABLE]
                 for k, o in enumerate(right)]
    peak = max(max(abs(v) for v in left), max(abs(v) for v in right)) or 1.0
    return [v / peak for v in left], [v / peak for v in right]


def _bell_envelope(length, total, bpm, sample_rate):
    """The bell's level at every sample: a 3 ms strike on each cut,
    decaying over _BELL_TAU. Identical on every cut."""
    shape = [((s / (0.003 * sample_rate)) if s < 0.003 * sample_rate
              else math.exp(-(s / float(sample_rate) - 0.003) / _BELL_TAU))
             for s in range(int(6 * _BELL_TAU * sample_rate))]
    env = [0.0] * length
    for start in ping_times(total, bpm):
        first = int(round(start * sample_rate))
        for j, v in enumerate(shape[:max(0, length - first)]):
            env[first + j] += v
    return env


def tone_level(t, total, bpm):
    """The pad's level (0..1) at video time `t`.

    Identical on every cut: a short swell from the sustain floor up to
    full, settling back over about a beat, then held -- never silent while
    the timer runs. 0:00 gets the same swell; after it the tone rings out
    over _RELEASE seconds and the hold goes quiet."""
    two_beats = 120.0 / bpm
    since = (t - total) % two_beats if t < total else t - total
    swell = (since / _ATTACK if since < _ATTACK
             else math.exp(-(since - _ATTACK) / _TAU))
    level = _FLOOR + (1.0 - _FLOOR) * swell
    if t < _FADE_IN:
        level *= t / _FADE_IN
    if t >= total:
        level *= math.exp(-(t - total) / _RELEASE)
    return level


def write_wav(path, duration, total, bpm, key, sample_rate=SAMPLE_RATE):
    """The dry track -- pad plus a bell on every cut -- in stereo, exactly
    `duration` seconds long so it ends with the video."""
    length = int(round(duration * sample_rate))
    left, right = _loop(key, _OCTAVES, _HARMONICS, sample_rate)
    bell_l, bell_r = _loop(key, _BELL, ((1, 1.0),), sample_rate)
    n = len(left)
    accent = _bell_envelope(length, total, bpm, sample_rate)
    # Pad at full swell plus the bell must stay inside _PEAK.
    scale = _PEAK / (1.0 + _BELL_LEVEL)
    # The last 30 ms fade to zero so a video that ends mid-tail (a short
    # hold) doesn't end on a click.
    fade_from = length - int(0.03 * sample_rate)
    pcm = array.array("h", [0] * (2 * length))
    for k in range(length):
        level = tone_level(k / float(sample_rate), total, bpm)
        fade = 1.0
        if k > fade_from:
            fade = (length - k) / float(length - fade_from)
        bell = _BELL_LEVEL * accent[k]
        i = k % n
        pcm[2 * k] = int(scale * fade * (level * left[i] + bell * bell_l[i])
                         * 32767)
        pcm[2 * k + 1] = int(scale * fade
                             * (level * right[i] + bell * bell_r[i]) * 32767)
    _write_pcm(path, pcm, 2, sample_rate)


def write_own_sound_wav(path, duration, total, bpm, sound,
                        sample_rate=SAMPLE_RATE):
    """The track for "My sound" (docs/specs/beat-opener.md): the stored
    clip (interleaved stereo, already trimmed so its attack is sample 0)
    starting exactly on every cut, as recorded.

    Each copy fades out over the 30 ms before the next cut: a sound longer
    than two beats would otherwise pile onto the next hit -- the smear the
    owner heard in the built-in tone's long reverb. The copy at 0:00 has
    no next cut and plays out into the hold."""
    length = int(round(duration * sample_rate))
    frames = len(sound) // 2
    mix = [0.0] * (2 * length)
    cuts = ping_times(total, bpm)
    fade = int(0.03 * sample_rate)
    for idx, start in enumerate(cuts):
        first = int(round(start * sample_rate))
        stop = length
        if idx + 1 < len(cuts):
            stop = min(stop, int(round(cuts[idx + 1] * sample_rate)))
        span = min(frames, stop - first)
        for j in range(max(0, span)):
            g = 1.0
            if first + span == stop and j >= span - fade and stop < length:
                g = (span - j) / float(fade)
            k = 2 * (first + j)
            mix[k] += g * sound[2 * j]
            mix[k + 1] += g * sound[2 * j + 1]
    # The file's last 30 ms fade so a short hold doesn't end on a click.
    for j in range(max(0, length - fade), length):
        g = (length - j) / float(fade)
        mix[2 * j] *= g
        mix[2 * j + 1] *= g
    pcm = array.array("h", (int(max(-32767, min(32767, v))) for v in mix))
    _write_pcm(path, pcm, 2, sample_rate)


def write_reverb_ir(path, sample_rate=SAMPLE_RATE, seed=7):
    """A stereo impulse response for ffmpeg's afir: decaying noise,
    darkened, a different stream per side so the room sounds wide.

    Seeded, so the same timer always renders the same audio."""
    rng = random.Random(seed)
    n = int(_REVERB_SECONDS * sample_rate)
    predelay = int(0.012 * sample_rate)
    pcm = array.array("h", [0] * (2 * n))
    for ch in (0, 1):
        lp = 0.0
        for k in range(n):
            if k < predelay:
                continue
            t = (k - predelay) / float(sample_rate)
            # One-pole low-pass: real rooms swallow highs first, and an
            # undarkened noise tail hisses.
            lp += 0.35 * (rng.uniform(-1.0, 1.0) - lp)
            pcm[2 * k + ch] = int(0.9 * lp * math.exp(-t / _REVERB_DECAY)
                                  * 32767)
    _write_pcm(path, pcm, 2, sample_rate)


def _write_pcm(path, pcm, channels, sample_rate):
    if sys.byteorder != "little":
        pcm.byteswap()           # WAV is little-endian on every machine
    with wave.open(path, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm.tobytes())


# Dry pad and reverb mixed, then limited. normalize=0 keeps amix from
# halving both inputs; level=0 keeps the limiter from re-gaining the
# result up to its ceiling. afir: irnorm=2 scales the impulse to unit
# energy so the tail comes out about as loud as the dry signal whatever
# noise was generated, and gtype=none turns off its automatic gain, which
# shrank a 2-second noise impulse to near silence. (Its "dry" option is
# the INPUT gain, not a dry/wet mix -- dry=0 silenced it completely.)
_AUDIO_GRAPH = (
    "[1:a]asplit=2[dry][send];"
    "[send][2:a]afir=gtype=none:irnorm=2[wet];"
    "[dry][wet]amix=inputs=2:weights=1 {mix}:normalize=0,"
    "alimiter=limit=0.89:level=0[a]").format(mix=_REVERB_MIX)


def add_sound(video_path, total, bpm, key, duration, own_sound=None):
    """Put the sound track into the finished, silent MP4 at `video_path`:
    the built-in pad (with its reverb), or `own_sound` -- the stored clip
    as interleaved stereo samples -- as recorded, with no effects.

    A second pass rather than a change to encoder.py: the video stream is
    copied untouched, so the countdown encoder -- byte-identical-guarded
    and the GPU path the owner just verified -- never learns this feature
    exists.
    """
    wav_path = video_path + ".wav.part"
    ir_path = video_path + ".ir.part"
    muxed = video_path + ".mux.part"
    try:
        if own_sound is not None:
            write_own_sound_wav(wav_path, duration, total, bpm, own_sound)
            audio = ["-f", "wav", "-i", wav_path, "-map", "0:v:0",
                     "-map", "1:a:0"]
        else:
            write_wav(wav_path, duration, total, bpm, key)
            write_reverb_ir(ir_path)
            audio = ["-f", "wav", "-i", wav_path, "-f", "wav", "-i", ir_path,
                     "-filter_complex", _AUDIO_GRAPH,
                     "-map", "0:v:0", "-map", "[a]"]
        extra = {}
        if sys.platform == "win32":
            extra["creationflags"] = 0x08000000      # CREATE_NO_WINDOW
        result = subprocess.run(
            # -nostdin: a --windowed Windows build has no console stdin,
            # and ffmpeg must never wait on one.
            [imageio_ffmpeg.get_ffmpeg_exe(), "-nostdin", "-y", "-v", "error",
             "-i", video_path] + audio +
            ["-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
             "-shortest", "-movflags", "+faststart", "-f", "mp4", muxed],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, **extra)
        if result.returncode != 0:
            raise RuntimeError(
                "Couldn't add the sound to the video (ffmpeg exit {0})."
                .format(result.returncode))
        replace_with_retry(muxed, video_path)
    finally:
        for leftover in (wav_path, ir_path, muxed):
            unlink_quietly(leftover)


def replace_with_retry(src, dst, attempts=5, wait=0.2):
    """os.replace, retried briefly. On Windows, Defender, the Search
    indexer or OneDrive (Documents is often synced on church PCs) can hold
    a just-written file for a moment, and os.replace then raises
    PermissionError -- which here would fail a finished export."""
    for attempt in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(wait)


def unlink_quietly(path):
    """Remove a temp file if it is there; a lock on a leftover temp file
    must never turn a good result into an error."""
    try:
        if os.path.exists(path):
            os.unlink(path)
    except OSError:
        pass


def filename_marker(bpm, key):
    """"_beat125A" / "_beat96Cs" -- '#' is not safe in every filename."""
    return "_beat{0}{1}".format(bpm, key.replace("#", "s"))
