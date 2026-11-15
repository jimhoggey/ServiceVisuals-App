"""Trim a downloaded clip (docs/specs/clip-trim.md).

The owner downloads a 15-minute video to use one minute of it. This cuts
a copy out of a file already in the exports folder; the original is never
touched. Not an editor -- one start, one end.
"""

import os
import re
import subprocess
import sys
import threading

import imageio_ffmpeg

from render.encoder import EXPORTS_DIR

TRIM_EXTS = (".mp4", ".mp3")
MIN_CLIP_SECONDS = 0.5

_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")


def _no_window():
    # Every other ffmpeg spawn in the app does this: on Windows a console
    # child opens a black window, and closing it kills the job.
    if sys.platform == "win32":
        return {"creationflags": 0x08000000}       # CREATE_NO_WINDOW
    return {}


def resolve_export(filename):
    """The real path of `filename` in the exports folder, or None.

    A bare name only: the containment check on the realpath is the actual
    boundary (a symlink or "..", however spelled, lands outside it)."""
    if not isinstance(filename, str) or not filename or len(filename) > 200:
        return None
    if filename != os.path.basename(filename) or "\\" in filename:
        return None
    if any(ord(c) < 32 for c in filename):
        return None
    root = os.path.realpath(EXPORTS_DIR)
    path = os.path.realpath(os.path.join(root, filename))
    if not path.startswith(root + os.sep) or not os.path.isfile(path):
        return None
    return path


def probe_duration(path):
    """Length in seconds from ffmpeg's own header read, or None.

    `ffmpeg -i` with no output exits 1 by design; the Duration line on
    stderr is all we want from it, and it costs ~50 ms."""
    try:
        proc = subprocess.run(
            [imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-i", path],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=20,
            **_no_window())
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = _DURATION_RE.search((proc.stderr or b"").decode("utf-8",
                                                            "replace"))
    if not match:
        return None
    h, m, s = match.groups()
    return int(h) * 3600 + int(m) * 60 + float(s)


def clock(seconds):
    """1:30 / 1:02:30 -- the way the trim panel shows a time."""
    total = int(round(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return "{0}:{1:02d}:{2:02d}".format(h, m, s)
    return "{0}:{1:02d}".format(m, s)


def _stamp(seconds):
    # No colons: Windows forbids them in a filename. "2m30s" reads fine.
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return "{0}h{1:02d}m{2:02d}s".format(h, m, s)
    return "{0}m{1:02d}s".format(m, s)


def trimmed_name(filename, start, end, exists=os.path.exists):
    """"Song.mp4" -> "Song (2m30s to 3m30s).mp4", then " 2", " 3" ... so
    trimming the same part twice never overwrites the first copy."""
    stem, ext = os.path.splitext(filename)
    base = "{0} ({1} to {2})".format(stem, _stamp(start), _stamp(end))
    # Keep the whole name inside EXPORT_FILENAME_RE's 200 characters, or
    # SHOW FILE would refuse to reveal the copy we just made.
    base = base[-(190 - len(ext)):] if len(base) > 190 - len(ext) else base
    name = base + ext
    n = 2
    while exists(os.path.join(EXPORTS_DIR, name)):
        name = "{0} {1}{2}".format(base, n, ext)
        n += 1
    return name


def _build_args(src, dst, start, length, ext):
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    args = [ffmpeg, "-hide_banner", "-nostats", "-y",
            "-ss", "{0:.3f}".format(start), "-i", src,
            "-t", "{0:.3f}".format(length)]
    if ext == ".mp3":
        # Audio frames are 26 ms, so a copy is already accurate -- and
        # lossless, and instant.
        args += ["-map", "0:a:0", "-c:a", "copy", "-f", "mp3"]
    else:
        # Re-encoded, not copied: a stream copy snaps the start back to the
        # previous keyframe, which on a YouTube file can be seconds early.
        args += ["-map", "0:v:0", "-map", "0:a:0?",
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                 "-pix_fmt", "yuv420p",
                 "-c:a", "aac", "-b:a", "192k",
                 "-movflags", "+faststart", "-f", "mp4"]
    args += ["-progress", "pipe:1", dst]
    return args


def trim_clip(options, progress_cb):
    """Job renderer: options already validated (validate_trim_options)."""
    src = options["path"]
    start = options["start"]
    end = options["end"]
    length = end - start
    ext = os.path.splitext(src)[1].lower()
    name = trimmed_name(os.path.basename(src), start, end)
    dst = os.path.join(EXPORTS_DIR, name)
    tmp = dst + ".part"

    proc = subprocess.Popen(
        _build_args(src, tmp, start, length, ext),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, **_no_window())
    tail = []

    def _pump_stderr():
        for raw in proc.stderr:
            tail.append(raw.decode("utf-8", "replace").rstrip())
            del tail[:-30]

    err_thread = threading.Thread(target=_pump_stderr, daemon=True)
    err_thread.start()
    for raw in proc.stdout:
        line = raw.decode("utf-8", "replace").strip()
        # out_time_us and out_time_ms are BOTH microseconds (an old ffmpeg
        # naming bug kept for compatibility).
        if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
            try:
                done = int(line.split("=", 1)[1]) / 1e6
            except ValueError:
                continue
            progress_cb(min(99, done / length * 100))
    proc.wait()
    err_thread.join(timeout=5)
    if proc.returncode != 0 or not os.path.isfile(tmp):
        try:
            os.unlink(tmp)
        except OSError:
            pass
        # ffmpeg's own words name the file, so they stay out of the
        # message (crash reports send its shape) -- a volunteer can act on
        # "download it again", not on a codec error.
        raise RuntimeError(
            "The clip couldn't be trimmed. Try downloading the video "
            "again, then trim the new copy.")
    os.replace(tmp, dst)
    progress_cb(100)
    return name
