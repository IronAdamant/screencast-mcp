"""Single-flight X11 recording session. One active capture; no stacking."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from screencast_mcp.media import (
    build_capture_command,
    build_encode_command,
    detect_video_size,
    probe_media,
    require_tool,
    validate_display,
)


class ToolFailure(Exception):
    """Domain error returned to the client as MCP isError plus JSON text."""

    def __init__(self, error: str, **extra):
        super().__init__(error)
        self.payload = {"ok": False, "error": error, **extra}


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _as_number(value, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ToolFailure(f"{name} must be a number")
    return float(value)


def default_out_dir(cwd: Optional[Path] = None) -> Path:
    root = Path(cwd or Path.cwd()) / ".screencast-runs"
    try:
        root.mkdir(parents=True, exist_ok=True)
        return root
    except OSError:
        fallback = Path(tempfile.gettempdir()) / "screencast-runs"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


@dataclass
class Recording:
    recording_id: str
    started_at: str
    started_monotonic: float
    max_seconds: float
    display: str
    raw_path: Path
    process: subprocess.Popen
    width: int
    height: int
    ended_monotonic: Optional[float] = None
    duration_seconds: Optional[float] = None
    saves: dict = field(default_factory=dict)


class ScreencastSession:
    def __init__(
        self,
        popen: Callable[..., subprocess.Popen] = subprocess.Popen,
        run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
        which: Optional[Callable[[str], Optional[str]]] = None,
        platform_name: Optional[str] = None,
        environ: Optional[dict] = None,
        detect_size: Optional[Callable[[str], tuple[int, int]]] = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._popen = popen
        self._run = run
        self._which = which
        self._platform = platform_name if platform_name is not None else sys.platform
        self._environ = environ if environ is not None else os.environ
        self._detect_size = detect_size or (lambda display: detect_video_size(display, run=self._run))
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._active: Optional[Recording] = None
        self._by_id: dict[str, Recording] = {}
        self._latest_id: Optional[str] = None

    def start(self, arguments: dict) -> dict:
        arguments = arguments or {}
        max_seconds = 60.0
        if "max_seconds" in arguments and arguments["max_seconds"] is not None:
            max_seconds = _as_number(arguments["max_seconds"], "max_seconds")
        if max_seconds <= 0 or max_seconds > 60:
            raise ToolFailure("max_seconds must be greater than 0 and at most 60")
        self._require_linux()
        display = arguments.get("display") or self._environ.get("DISPLAY") or ""
        try:
            display = validate_display(display)
        except ValueError as exc:
            raise ToolFailure(str(exc)) from exc
        out_dir = Path(arguments["out_dir"]) if arguments.get("out_dir") else default_out_dir()
        with self._lock:
            self._reap_locked()
            if self._active is not None:
                raise ToolFailure(
                    "a recording is already active; call screencast_stop before starting another",
                    recording_id=self._active.recording_id,
                )
            ffmpeg = self._tool("ffmpeg")
            width, height = self._detect_size(display)
            recording_id = uuid.uuid4().hex[:12]
            run_dir = out_dir / recording_id
            run_dir.mkdir(parents=True, exist_ok=True)
            raw_path = run_dir / "capture.mp4"
            command = build_capture_command(
                ffmpeg, display, width, height, max_seconds, str(raw_path)
            )
            try:
                process = self._popen(
                    command,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    start_new_session=True,
                )
            except OSError as exc:
                raise ToolFailure(f"failed to start ffmpeg: {exc}") from exc
            recording = Recording(
                recording_id=recording_id,
                started_at=_utc_now(),
                started_monotonic=self._clock(),
                max_seconds=max_seconds,
                display=display,
                raw_path=raw_path,
                process=process,
                width=width,
                height=height,
            )
            self._sleep(0.35)
            if process.poll() not in (None, 0):
                err = _stderr_tail(process)
                raise ToolFailure(
                    f"ffmpeg exited immediately (code {process.returncode}). {err}".strip()
                )
            self._active = recording
            self._by_id[recording_id] = recording
            self._latest_id = recording_id
            timer = threading.Timer(max_seconds + 1.5, self._auto_stop, args=(recording_id,))
            timer.daemon = True
            timer.start()
        return {
            "ok": True,
            "recording_id": recording_id,
            "started_at": recording.started_at,
            "max_seconds": max_seconds,
            "display": display,
            "raw_path": str(raw_path),
        }

    def stop(self, arguments: Optional[dict] = None) -> dict:
        del arguments
        with self._lock:
            self._reap_locked()
            recording = self._active
            if recording is None:
                raise ToolFailure("no active recording")
            self._active = None
        self._finish(recording)
        return {
            "ok": True,
            "recording_id": recording.recording_id,
            "duration_seconds": recording.duration_seconds,
            "raw_path": str(recording.raw_path),
        }

    def save(self, arguments: dict) -> dict:
        arguments = arguments or {}
        fmt = arguments.get("format")
        if not isinstance(fmt, str) or fmt.lower() not in ("gif", "mp4"):
            raise ToolFailure("format is required and must be 'gif' or 'mp4'")
        fmt = fmt.lower()
        fps = None
        if arguments.get("fps") is not None:
            fps = _as_number(arguments["fps"], "fps")
            if fps < 1 or fps > 30:
                raise ToolFailure("fps must be between 1 and 30")
        elif fmt == "gif":
            fps = 12.0
        scale_width = None
        if arguments.get("scale_width") is not None:
            scale_width = int(_as_number(arguments["scale_width"], "scale_width"))
            if scale_width < 2 or scale_width > 1920:
                raise ToolFailure("scale_width must be between 2 and 1920")
        bezel = arguments.get("bezel") or "none"
        if bezel not in ("none", "phone", "desktop"):
            raise ToolFailure("bezel must be one of: none, phone, desktop")
        with self._lock:
            self._reap_locked()
            recording = self._resolve_locked(arguments.get("recording_id"))
            if self._active is not None and recording.recording_id == self._active.recording_id:
                raise ToolFailure(
                    "recording is still active; call screencast_stop before save",
                    recording_id=recording.recording_id,
                )
        if not recording.raw_path.is_file() or recording.raw_path.stat().st_size == 0:
            raise ToolFailure("raw capture is missing or empty", raw_path=str(recording.raw_path))
        ffmpeg = self._tool("ffmpeg")
        out_path = (
            Path(arguments["out_path"])
            if arguments.get("out_path")
            else recording.raw_path.with_name(f"screencast.{fmt}")
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        command = build_encode_command(
            ffmpeg,
            str(recording.raw_path),
            str(out_path),
            fmt,
            fps,
            scale_width,
            bezel,
        )
        completed = self._run(command, check=False, capture_output=True, text=True, timeout=120)
        if completed.returncode != 0 or not out_path.is_file():
            detail = (completed.stderr or "ffmpeg encode failed").strip()[-400:]
            raise ToolFailure(f"encode failed: {detail}")
        info = self._probe(out_path)
        duration = info.get("duration_seconds")
        if duration is None:
            duration = recording.duration_seconds
        payload = {
            "ok": True,
            "format": fmt,
            "path": str(out_path),
            "bytes": out_path.stat().st_size,
            "duration_seconds": duration,
            "width": info.get("width") or recording.width,
            "height": info.get("height") or recording.height,
            "bezel": bezel,
        }
        recording.saves[fmt] = str(out_path)
        return payload

    def status(self, arguments: Optional[dict] = None) -> dict:
        del arguments
        with self._lock:
            self._reap_locked()
            active = self._active
            latest = self._by_id.get(self._latest_id) if self._latest_id else None
            state = "recording" if active is not None else "idle"
            body = {
                "ok": True,
                "state": state,
                "recording_id": active.recording_id if active else None,
                "display": active.display if active else None,
                "started_at": active.started_at if active else None,
                "max_seconds": active.max_seconds if active else None,
                "raw_path": str(active.raw_path) if active else None,
                "last": _public_recording(latest) if latest and latest is not active else (
                    _public_recording(latest) if latest else None
                ),
            }
            if active is not None:
                body["last"] = _public_recording(latest) if latest and latest is not active else None
            return body

    def _resolve_locked(self, recording_id: Optional[str]) -> Recording:
        if recording_id:
            found = self._by_id.get(recording_id)
            if found is None:
                raise ToolFailure(f"unknown recording_id {recording_id}")
            return found
        if self._latest_id and self._latest_id in self._by_id:
            return self._by_id[self._latest_id]
        raise ToolFailure("no recording to save; call screencast_start first")

    def _reap_locked(self) -> None:
        active = self._active
        if active is not None and active.process.poll() is not None:
            self._active = None
            self._mark_finished(active)

    def _auto_stop(self, recording_id: str) -> None:
        with self._lock:
            active = self._active
            if active is None or active.recording_id != recording_id:
                return
            if active.process.poll() is not None:
                self._active = None
                self._mark_finished(active)
                return
            self._active = None
        self._finish(active)

    def _finish(self, recording: Recording) -> None:
        _stop_process(recording.process)
        self._mark_finished(recording)

    def _mark_finished(self, recording: Recording) -> None:
        if recording.ended_monotonic is None:
            recording.ended_monotonic = self._clock()
        wall = max(0.0, recording.ended_monotonic - recording.started_monotonic)
        probed = None
        if recording.raw_path.is_file() and recording.raw_path.stat().st_size > 0:
            try:
                probed = self._probe(recording.raw_path).get("duration_seconds")
            except (ToolFailure, OSError, ValueError):
                probed = None
        recording.duration_seconds = round(probed if probed is not None else wall, 3)

    def _probe(self, path: Path) -> dict:
        ffprobe = self._tool("ffprobe")
        try:
            return probe_media(str(path), ffprobe, run=self._run)
        except (OSError, subprocess.SubprocessError, ValueError, RuntimeError) as exc:
            raise ToolFailure(f"ffprobe failed: {exc}") from exc

    def _tool(self, name: str) -> str:
        try:
            return require_tool(name, which=self._which) if self._which else require_tool(name)
        except RuntimeError as exc:
            raise ToolFailure(str(exc)) from exc

    def _require_linux(self) -> None:
        if not str(self._platform).startswith("linux"):
            raise ToolFailure(
                f"v1 records X11 on Linux only (this host is {self._platform}). "
                "macOS and Windows are out of scope."
            )


def _public_recording(recording: Optional[Recording]) -> Optional[dict]:
    if recording is None:
        return None
    return {
        "recording_id": recording.recording_id,
        "raw_path": str(recording.raw_path),
        "duration_seconds": recording.duration_seconds,
        "display": recording.display,
        "started_at": recording.started_at,
        "saves": dict(recording.saves),
    }


def _stderr_tail(process: subprocess.Popen) -> str:
    try:
        if process.stderr:
            data = process.stderr.read()
            if isinstance(data, bytes):
                return data.decode("utf-8", "replace")[-400:]
    except Exception:
        return ""
    return ""


def _stop_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        if process.stdin:
            process.stdin.write(b"q\n")
            process.stdin.flush()
    except Exception:
        pass
    try:
        process.wait(timeout=3)
        return
    except subprocess.TimeoutExpired:
        pass
    for action, timeout in (
        (lambda: process.send_signal(signal.SIGINT), 2),
        (process.terminate, 2),
        (process.kill, 2),
    ):
        if process.poll() is not None:
            return
        try:
            action()
            process.wait(timeout=timeout)
            return
        except subprocess.TimeoutExpired:
            continue
        except Exception:
            continue
