"""FrameEncoder: pipes raw RGB frames from Pillow into the bundled ffmpeg.

Uses the static ffmpeg binary shipped by the imageio-ffmpeg pip package, so
nothing needs to be installed system-wide. Output is H.264 / yuv420p /
+faststart MP4 at a constant 30 fps — the most ProPresenter-compatible combo.

Renderers may feed frames at a lower input fps (e.g. 1 fps for a digits-only
timer); ffmpeg duplicates frames up to the 30 fps output.
"""

import os
import re
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

import imageio_ffmpeg

WIDTH = 1920
HEIGHT = 1080
OUTPUT_FPS = 30


def _default_exports_dir():
    """Single source of truth for where finished MP4s land.

    Running from source: <repo>/exports. Packaged app (PyInstaller): a
    visible folder in the user's Documents, since the bundle dir is not a
    sane place for user files. Overridable via SERVICE_VISUALS_EXPORTS.
    """
    override = os.environ.get("SERVICE_VISUALS_EXPORTS")
    if override:
        return os.path.abspath(override)
    if getattr(sys, "frozen", False):
        return os.path.join(
            os.path.expanduser("~"), "Documents", "Service Visuals")
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "exports")


EXPORTS_DIR = _default_exports_dir()

# Uploaded background images live in a temp dir (they are inputs, not outputs,
# so they don't belong next to the user's finished MP4s). Cleared by the OS.
UPLOADS_DIR = os.path.join(tempfile.gettempdir(), "service-visuals-uploads")


def export_path(prefix, descriptor, ext=".mp4"):
    """Build a unique, filesystem-safe path in exports/.

    e.g. export_path("timer", "5m00s_ring") ->
         .../exports/timer_5m00s_ring_20260704-103000.mp4
    `ext` lets the QR card also export a still .png (nothing moves in it).
    """
    os.makedirs(EXPORTS_DIR, exist_ok=True)
    descriptor = re.sub(r"[^A-Za-z0-9_-]+", "-", descriptor).strip("-")[:60]
    stamp = time.strftime("%Y%m%d-%H%M%S")
    base = f"{prefix}_{descriptor}_{stamp}"
    path = os.path.join(EXPORTS_DIR, base + ext)
    n = 2
    while os.path.exists(path):
        path = os.path.join(EXPORTS_DIR, f"{base}_{n}{ext}")
        n += 1
    return path


def encode_parallel(out_path, input_fps, total_frames, make_frame,
                    progress_cb=None, output_fps=OUTPUT_FPS,
                    alpha_format=None):
    """Generate frames on a thread pool and write them to ffmpeg IN ORDER.

    Pillow releases the GIL inside its C image routines, so threads give real
    parallelism here (~4x on a 8-core machine) without the process-spawning
    hazards that multiprocessing brings to a PyInstaller bundle (especially
    Windows --onefile, where each child would re-extract the whole app).

    make_frame(k) must only READ shared images and return a fresh frame, so
    frames can be built concurrently. Work is done in small batches so at most
    ~2 frames per worker are ever in memory (a 1080p RGB frame is ~6 MB).

    `alpha_format` (docs/specs/alpha-export.md) is forwarded straight to
    FrameEncoder untouched — None means the existing RGB/MP4 path, so every
    caller that never passes it is byte-identical to before this feature
    existed.
    """
    workers = max(1, min(6, (os.cpu_count() or 2) - 1))
    with FrameEncoder(out_path, input_fps, output_fps=output_fps,
                      alpha_format=alpha_format) as enc:
        if workers == 1:
            for k in range(total_frames):
                enc.add_frame(make_frame(k))
                if progress_cb:
                    progress_cb(int((k + 1) * 100.0 / total_frames))
            return
        batch = workers * 2
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for start in range(0, total_frames, batch):
                idx = range(start, min(start + batch, total_frames))
                for k, frame in zip(idx, pool.map(make_frame, idx)):
                    enc.add_frame(frame)
                    if progress_cb:
                        progress_cb(int((k + 1) * 100.0 / total_frames))


# ---------------------------------------------------------------- codec pick
# On Windows, gaming machines have hardware H.264 encoders (NVIDIA NVENC,
# Intel QuickSync, AMD AMF) that encode 1080p several times faster than
# software libx264 — the same path real editors and OBS use. We probe once
# per process with a tiny throwaway encode and cache the first codec that
# actually works, falling back to libx264. macOS stays on libx264: Apple
# Silicon runs it extremely fast, and VideoToolbox benchmarked SLOWER here.
# Override with SERVICE_VISUALS_ENCODER=libx264 (etc.) if ever needed.

_CODEC_FLAGS = {
    "libx264": ["-preset", "veryfast", "-crf", "19"],
    "h264_nvenc": ["-preset", "p4", "-rc", "vbr", "-cq", "19", "-b:v", "0"],
    "h264_qsv": ["-preset", "veryfast", "-global_quality", "19"],
    "h264_amf": ["-quality", "balanced", "-rc", "cqp",
                 "-qp_i", "19", "-qp_p", "21"],
}

_picked_codec = None


def _probe_codec(codec):
    """Can this ffmpeg + this machine actually encode with `codec`?"""
    cmd = [
        imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error",
        "-f", "lavfi", "-i", "color=c=black:s=256x256:r=30:d=0.2",
        "-vcodec", codec, *_CODEC_FLAGS[codec],
        "-pix_fmt", "yuv420p", "-f", "null", "-",
    ]
    try:
        extra = {}
        if sys.platform == "win32":
            extra["creationflags"] = 0x08000000      # CREATE_NO_WINDOW
        return subprocess.run(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=20, **extra).returncode == 0
    except Exception:
        return False


def pick_codec():
    """The fastest working H.264 encoder on this machine (cached)."""
    global _picked_codec
    if _picked_codec is None:
        override = os.environ.get("SERVICE_VISUALS_ENCODER", "").strip()
        if override in _CODEC_FLAGS:
            _picked_codec = override
        else:
            candidates = (["h264_nvenc", "h264_qsv", "h264_amf", "libx264"]
                          if sys.platform == "win32" else ["libx264"])
            _picked_codec = "libx264"
            for codec in candidates:
                if _probe_codec(codec):
                    _picked_codec = codec
                    break
    return _picked_codec


class EncoderError(RuntimeError):
    pass


# ---------------------------------------------------------- alpha export
# docs/specs/alpha-export.md. A FIXED codec per format -- chosen for what
# ProPresenter/CapCut can actually open (confirmed by dropping real
# exported clips into CapCut; earlier research assumed WebM would also
# work here and it does not -- CapCut showed it as a solid white box),
# never probed or picked for speed -- so this is a completely separate
# table from _CODEC_FLAGS above, which exists ONLY to pick the fastest
# available H.264 encoder.
ALPHA_FORMATS = {
    "qtrle": {
        # QuickTime Animation (RLE) -- lossless. The default: CapCut-
        # confirmed transparent, and roughly a third the size of ProRes
        # on real timer content (see the spec's Why section) because RLE
        # compresses the plate/track's large flat runs far harder than
        # ProRes's intra-frame DCT does. NOT on ProPresenter's documented
        # supported-format list (H.264, HEVC, ProRes variants, HAP), and
        # nobody has tested it there -- neither this comment nor the UI
        # copy may claim it works in ProPresenter.
        "vcodec": "qtrle",
        "flags": [],
        "pix_fmt": "argb",
        "container": "mov",
        "ext": ".mov",
        "movflags": True,
    },
    "prores": {
        # The maximum-compatibility choice: CapCut-confirmed AND on
        # ProPresenter's documented supported-format list. Pick this one
        # whenever the file is going straight into ProPresenter.
        "vcodec": "prores_ks",
        "flags": ["-profile:v", "4444"],
        "pix_fmt": "yuva444p10le",
        "container": "mov",
        "ext": ".mov",
        "movflags": True,   # meaningful for a mov/mp4-family muxer
    },
}


class FrameEncoder:
    """Context manager that encodes PIL frames to a video file.

    Usage:
        with FrameEncoder("/path/out.mp4", input_fps=10) as enc:
            enc.add_frame(pil_image)   # 1920x1080, mode RGB

    With `alpha_format` set (docs/specs/alpha-export.md) frames must be
    RGBA instead, and the file written is a .mov carrying real alpha —
    see ALPHA_FORMATS above.
    """

    def __init__(self, out_path, input_fps, width=WIDTH, height=HEIGHT,
                 output_fps=OUTPUT_FPS, alpha_format=None):
        self.out_path = out_path
        # Encode to a temporary *.part name and os.replace() it into place
        # only on success, so a killed process (Ctrl+C mid-render) can never
        # leave a playable-but-truncated MP4 under the final filename.
        self._tmp_path = out_path + ".part"
        self.width = width
        self.height = height
        self.frames_written = 0
        self._alpha_format = alpha_format
        # ffmpeg writes progress chatter to stderr; buffer it in a temp file
        # so the pipe can never fill up and deadlock us.
        self._stderr = tempfile.TemporaryFile()

        if alpha_format is None:
            # UNCHANGED from before alpha export, character for character --
            # do not fold this into a shared table with ALPHA_FORMATS above.
            # Calling pick_codec() once here instead of twice (the old code
            # called it once for -vcodec and again to index _CODEC_FLAGS)
            # changes nothing observable: pick_codec() is memoized at
            # module level, so both calls already returned the same cached
            # value.
            in_pix_fmt = "rgb24"
            vcodec = pick_codec()
            codec_args = ["-vcodec", vcodec, *_CODEC_FLAGS[vcodec],
                          "-pix_fmt", "yuv420p"]
            container = "mp4"
            movflags = True
        else:
            # Alpha export (docs/specs/alpha-export.md). Deliberately never
            # calls pick_codec()/_probe_codec() or touches _CODEC_FLAGS:
            # pick_codec()'s Windows candidates (h264_nvenc/h264_qsv/
            # h264_amf) are H.264-only hardware encoders that cannot
            # produce alpha at all, _CODEC_FLAGS has no entry for
            # prores_ks/qtrle (a lookup would KeyError), and probing
            # costs a real subprocess spawn (up to a 20s timeout) to
            # "discover" a choice that was never in question -- there is
            # no GPU-accelerated ProRes or RLE encoder to find on a
            # volunteer's laptop, and this path picks its codec from the
            # format the operator chose, not from what is fastest.
            fmt_spec = ALPHA_FORMATS[alpha_format]
            in_pix_fmt = "rgba"
            codec_args = ["-vcodec", fmt_spec["vcodec"], *fmt_spec["flags"],
                          "-pix_fmt", fmt_spec["pix_fmt"]]
            container = fmt_spec["container"]
            movflags = fmt_spec["movflags"]

        cmd = [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-y",
            "-f", "rawvideo",
            "-pix_fmt", in_pix_fmt,
            "-s", f"{width}x{height}",
            "-r", str(input_fps),
            "-i", "-",
            "-an",
            *codec_args,
            "-r", str(output_fps),
        ]
        if movflags:
            cmd += ["-movflags", "+faststart"]
        cmd += ["-f", container, self._tmp_path]   # .part hides the ext
        # On Windows the app is built --windowed (no console), so spawning
        # ffmpeg normally flashes a console window for EVERY render. Suppress
        # it the same way updater.py does for its helper script.
        extra = {}
        if sys.platform == "win32":
            extra["creationflags"] = 0x08000000      # CREATE_NO_WINDOW
        self._proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
            stderr=self._stderr, **extra
        )

    def add_frame(self, image):
        if image.size != (self.width, self.height):
            raise EncoderError(
                f"frame is {image.size}, expected {(self.width, self.height)}")
        if self._alpha_format is None:
            if image.mode != "RGB":
                image = image.convert("RGB")
        elif image.mode != "RGBA":
            # A renderer bug, not an operator mistake -- silently
            # converting (today's non-alpha rule, above) would flatten or
            # discard the alpha this whole feature exists to keep, so this
            # fails loudly instead, the same way the size check above does.
            raise EncoderError(
                f"alpha frame is {image.mode}, expected RGBA")
        try:
            self._proc.stdin.write(image.tobytes())
        except BrokenPipeError:
            raise EncoderError(
                "ffmpeg exited early: " + self._stderr_tail()) from None
        self.frames_written += 1

    def close(self):
        if self._proc.stdin and not self._proc.stdin.closed:
            self._proc.stdin.close()
        code = self._proc.wait()
        tail = self._stderr_tail()
        self._stderr.close()
        if code != 0:
            # Don't leave a truncated MP4 lying around for the user to import.
            if os.path.exists(self._tmp_path):
                os.unlink(self._tmp_path)
            raise EncoderError(f"ffmpeg failed (exit {code}): {tail}")
        os.replace(self._tmp_path, self.out_path)

    def abort(self):
        """Kill ffmpeg and delete partial output (used on renderer errors)."""
        try:
            try:
                if self._proc.stdin and not self._proc.stdin.closed:
                    self._proc.stdin.close()
            except OSError:
                pass  # flushing into a dying ffmpeg can raise BrokenPipeError
            self._proc.kill()
            self._proc.wait()
        finally:
            self._stderr.close()
            if os.path.exists(self._tmp_path):
                os.unlink(self._tmp_path)

    def _stderr_tail(self, limit=800):
        try:
            self._stderr.seek(0)
            data = self._stderr.read().decode("utf-8", "replace")
            return data[-limit:].strip()
        except ValueError:  # already closed
            return ""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.close()
        else:
            self.abort()
        return False
