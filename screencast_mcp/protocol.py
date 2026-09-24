"""MCP stdio JSON-RPC (newline-delimited, plus Content-Length read support)."""

from __future__ import annotations

import json
from typing import Any, BinaryIO, Optional

from screencast_mcp import __version__
from screencast_mcp.session import ScreencastSession, ToolFailure

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER_NAME = "screencast-mcp"

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


def _tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
    schema: dict[str, Any] = {"type": "object", "properties": properties, "additionalProperties": False}
    if required:
        schema["required"] = required
    return {"name": name, "description": description, "inputSchema": schema}


TOOLS = [
    _tool(
        "screencast_start",
        "Start a continuous X11 viewport recording (ffmpeg x11grab). "
        "Hard cap 60 seconds. Fails if a recording is already active. "
        "Records whatever is on the display, including secrets if they are visible.",
        {
            "max_seconds": {"type": "number", "description": "Stop after this many seconds. Default 60. Maximum 60."},
            "display": {"type": "string", "description": "X11 display, for example :31. Defaults to $DISPLAY."},
            "out_dir": {"type": "string", "description": "Directory for the raw capture. Default ./.screencast-runs."},
        },
        [],
    ),
    _tool(
        "screencast_stop",
        "Stop the active recording early. Errors if nothing is recording.",
        {},
        [],
    ),
    _tool(
        "screencast_save",
        "Encode the raw capture to GIF or MP4 with ffmpeg. "
        "bezel is best-effort: phone center-crops toward 9:16, desktop adds a thin letterbox.",
        {
            "format": {"type": "string", "enum": ["gif", "mp4"], "description": "Output container."},
            "recording_id": {"type": "string", "description": "Recording to encode. Defaults to the latest."},
            "out_path": {"type": "string", "description": "Destination file. Default sits next to the raw capture."},
            "fps": {"type": "number", "description": "GIF frame rate. Default 12. MP4 keeps the source frame rate."},
            "scale_width": {"type": "number", "description": "Output width in pixels. Height follows aspect ratio."},
            "bezel": {"type": "string", "enum": ["none", "phone", "desktop"], "description": "Best-effort framing. Default none."},
        },
        ["format"],
    ),
    _tool(
        "screencast_status",
        "Report idle or recording, plus the last capture paths.",
        {},
        [],
    ),
]

TOOL_BY_NAME = {tool["name"]: tool for tool in TOOLS}


def tool_result(payload: dict, is_error: bool = False) -> dict:
    return {
        "content": [{"type": "text", "text": json.dumps(payload, separators=(",", ":"))}],
        "isError": is_error,
    }


def read_message(stream: BinaryIO) -> Optional[dict]:
    """Read one JSON-RPC message. Supports NDJSON and LSP Content-Length framing."""
    while True:
        line = stream.readline()
        if line == b"":
            return None
        if not line.strip():
            continue
        if line.lower().startswith(b"content-length:"):
            length = int(line.split(b":", 1)[1].strip())
            while True:
                header = stream.readline()
                if header in (b"\n", b"\r\n", b""):
                    break
            body = stream.read(length)
            message = json.loads(body.decode("utf-8"))
            if not isinstance(message, dict):
                raise ValueError("message must be a JSON object")
            return message
        message = json.loads(line.decode("utf-8"))
        if not isinstance(message, dict):
            raise ValueError("message must be a JSON object")
        return message


def write_message(stream: BinaryIO, message: dict) -> None:
    data = json.dumps(message, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    stream.write(data + b"\n")
    stream.flush()


class McpServer:
    def __init__(self, session: Optional[ScreencastSession] = None):
        self.session = session or ScreencastSession()
        self.initialized = False
        self.protocol_version = PROTOCOL_VERSIONS[0]

    def handle(self, message: dict) -> Optional[dict]:
        if not isinstance(message, dict):
            return _error(None, INVALID_REQUEST, "invalid request")
        method = message.get("method")
        msg_id = message.get("id", None)
        has_id = "id" in message and msg_id is not None
        if not isinstance(method, str):
            if has_id:
                return _error(msg_id, INVALID_REQUEST, "invalid request")
            return None
        params = message.get("params") or {}
        if params is None:
            params = {}
        if not isinstance(params, dict):
            if has_id:
                return _error(msg_id, INVALID_PARAMS, "params must be an object")
            return None
        try:
            result = self._dispatch(method, params)
        except ToolFailure as exc:
            if not has_id:
                return None
            return {"jsonrpc": "2.0", "id": msg_id, "result": tool_result(exc.payload, True)}
        except _Protocol as exc:
            if not has_id:
                return None
            return _error(msg_id, exc.code, exc.rpc_message)
        except Exception as exc:  # noqa: BLE001 — last-resort protocol error, never stdout noise
            if not has_id:
                return None
            return _error(msg_id, INTERNAL_ERROR, str(exc))
        if not has_id:
            return None
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    def _dispatch(self, method: str, params: dict) -> Any:
        if method == "initialize":
            return self._initialize(params)
        if method in ("notifications/initialized", "initialized", "notifications/cancelled"):
            return None
        if method == "ping":
            self._require_initialized()
            return {}
        if not self.initialized and method != "initialize":
            raise _Protocol(INVALID_REQUEST, "server not initialized")
        if method == "tools/list":
            return {"tools": TOOLS}
        if method == "tools/call":
            return self._call(params)
        raise _Protocol(METHOD_NOT_FOUND, f"method not found: {method}")

    def _initialize(self, params: dict) -> dict:
        requested = params.get("protocolVersion")
        if requested in PROTOCOL_VERSIONS:
            self.protocol_version = requested
        else:
            self.protocol_version = PROTOCOL_VERSIONS[0]
        self.initialized = True
        return {
            "protocolVersion": self.protocol_version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": __version__},
            "instructions": (
                "Records the X11 display for at most 60 seconds and saves a GIF or MP4. "
                "The operator must keep secrets and passwords off screen. "
                "Linux/X11 only in v1. ffmpeg and ffprobe must be on PATH."
            ),
        }

    def _require_initialized(self) -> None:
        if not self.initialized:
            raise _Protocol(INVALID_REQUEST, "server not initialized")

    def _call(self, params: dict) -> dict:
        name = params.get("name")
        if name not in TOOL_BY_NAME:
            raise _Protocol(METHOD_NOT_FOUND, f"unknown tool: {name}")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            raise _Protocol(INVALID_PARAMS, "arguments must be an object")
        handler = {
            "screencast_start": self.session.start,
            "screencast_stop": self.session.stop,
            "screencast_save": self.session.save,
            "screencast_status": self.session.status,
        }[name]
        payload = handler(arguments)
        return tool_result(payload, False)


class _Protocol(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.rpc_message = message


def _error(msg_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def serve(stdin: BinaryIO, stdout: BinaryIO, server: Optional[McpServer] = None) -> None:
    mcp = server or McpServer()
    while True:
        try:
            message = read_message(stdin)
        except json.JSONDecodeError:
            write_message(stdout, _error(None, PARSE_ERROR, "parse error"))
            continue
        except ValueError as exc:
            write_message(stdout, _error(None, INVALID_REQUEST, str(exc)))
            continue
        if message is None:
            return
        try:
            response = mcp.handle(message)
        except _Protocol as exc:
            msg_id = message.get("id")
            if "id" in message and msg_id is not None:
                write_message(stdout, _error(msg_id, exc.code, exc.rpc_message))
            continue
        if response is not None:
            write_message(stdout, response)
