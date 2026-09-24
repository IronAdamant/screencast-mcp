"""ffmpeg/ffprobe command builders and media probes. No shell interpolation."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from typing import Callable, Optional

_DISPLAY_RE = re.compile(r"^(?:[A-Za-z0-9._-]+:|:)\d+(?:\.\d+)?\Z")
_SIZE_RE = re.compile(r"dimensions:\s+(\d+)x(\d+)")


def require_tool(name: str, which: Callable[[str], Optional[str]] = shutil.which) -> str:
    path = which(name)
    if not path:
        raise RuntimeError(
            f"{name} is not on PATH. Install ffmpeg (which provides {name}) and retry."
        )
    return path


def validate_display(display: str) -> str:
    if not isinstance(display, str) or not _DISPLAY_RE.match(display):
        raise ValueError(
            "display must look like :0, :31, or :0.0 (X11). "
            "macOS and Windows capture are out of scope for v1."
        )
    return display


def even(value: int) -> int:
    value = int(value)
    if value < 2:
        return 2
    return value if value % 2 == 0 else value - 1


def detect_video_size(
    display: str,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> tuple[int, int]:
    """Read the X11 root window size. Falls back to 1920x1080."""
    try:
        completed = run(
            ["xdpyinfo", "-display", display],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        match = _SIZE_RE.search(completed.stdout or "")
        if match:
            return even(int(match.group(1))), even(int(match.group(2)))
    except (OSError, subprocess.SubprocessError):
        pass
    return 1920, 1080


def build_capture_command(
    ffmpeg: str,
    display: str,
    width: int,
    height: int,
    max_seconds: float,
    raw_path: str,
    framerate: int = 15,
) -> list[str]:
    """Continuous x11grab into a fragmented MP4 that survives an early stop."""
    return [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "x11grab",
        "-framerate",
        str(framerate),
        "-video_size",
        f"{even(width)}x{even(height)}",
        "-i",
        display,
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-tune",
        "zerolatency",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+frag_keyframe+empty_moov+default_base_moof",
        "-t",
        f"{max_seconds:.3f}",
        raw_path,
    ]


def build_video_filter(
    fmt: str,
    fps: Optional[float],
    scale_width: Optional[int],
    bezel: str,
) -> Optional[str]:
    """Best-effort scale / bezel filter. Phone crops toward 9:16; desktop adds a thin bar."""
    parts: list[str] = []
    if fmt == "gif":
        rate = 12.0 if fps is None else float(fps)
        parts.append(f"fps={rate:.3f}")
    if scale_width is not None:
        parts.append(f"scale={even(scale_width)}:-2:flags=lanczos")
    if bezel == "phone":
        # Center-crop toward a portrait 9:16 frame. Commas inside min() are escaped.
        parts.append(
            "crop=trunc(min(iw\\,ih*9/16)/2)*2:trunc(min(ih\\,iw*16/9)/2)*2"
        )
    elif bezel == "desktop":
        parts.append("pad=iw:ih+32:0:16:color=0x111111")
    elif bezel != "none":
        raise ValueError("bezel must be one of: none, phone, desktop")
    if fmt == "gif":
        chain = ",".join(parts) if parts else "fps=12"
        return (
            f"{chain},split[s0][s1];[s0]palettegen=stats_mode=diff[p];"
            "[s1][p]paletteuse=dither=bayer:bayer_scale=3"
        )
    if not parts:
        return None
    parts.append("scale=trunc(iw/2)*2:trunc(ih/2)*2")
    return ",".join(parts)


def build_encode_command(
    ffmpeg: str,
    raw_path: str,
    out_path: str,
    fmt: str,
    fps: Optional[float],
    scale_width: Optional[int],
    bezel: str,
) -> list[str]:
    if fmt not in ("gif", "mp4"):
        raise ValueError("format must be gif or mp4")
    command = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        raw_path,
        "-t",
        "60",
        "-an",
    ]
    video_filter = build_video_filter(fmt, fps, scale_width, bezel)
    if video_filter:
        command.extend(["-vf", video_filter])
    if fmt == "gif":
        command.extend(["-loop", "0", out_path])
    else:
        command.extend(
            [
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                out_path,
            ]
        )
    return command


def probe_media(
    path: str,
    ffprobe: str,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> dict:
    """Return duration_seconds, width, height. Missing fields stay None."""
    command = [
        ffprobe,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,duration",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        path,
    ]
    completed = run(command, check=False, capture_output=True, text=True, timeout=30)
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or "ffprobe failed").strip()[-400:])
    payload = json.loads(completed.stdout or "{}")
    streams = payload.get("streams") or [{}]
    stream = streams[0] if streams else {}
    duration = stream.get("duration") or (payload.get("format") or {}).get("duration")
    return {
        "duration_seconds": float(duration) if duration not in (None, "N/A") else None,
        "width": stream.get("width"),
        "height": stream.get("height"),
    }

