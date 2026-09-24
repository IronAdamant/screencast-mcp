# screencast-mcp

Updated: 2026-09-24

Purpose: stdio MCP server that records an X11 display for at most 60 seconds and encodes GIF or MP4 with system ffmpeg.

## Files

- `server.py` — executable entrypoint. Adds the repo root to `sys.path` and serves MCP on stdio.
- `screencast_mcp/__init__.py` — package version.
- `screencast_mcp/protocol.py` — JSON-RPC framing, tool schemas, `initialize` / `tools/list` / `tools/call`.
- `screencast_mcp/session.py` — one active recording, start/stop/save/status, ffmpeg process control.
- `screencast_mcp/media.py` — argv builders for x11grab, GIF/MP4 encode, ffprobe, display validation.
- `tests/test_media.py` — command and display validation.
- `tests/test_protocol.py` — schemas, framing, dispatch.
- `tests/test_session.py` — fake ffmpeg process, stacking and cap rules.
- `tests/test_stdio_smoke.py` — spawns `server.py`.
- `tests/test_live_capture.py` — real DISPLAY capture when available.
- `README.md` — install and tool docs.
- `LICENSE` — MIT.
- `.gitignore` — captures, bytecode.

Data flow: MCP client → `server.py` → `protocol.McpServer` → `session.ScreencastSession` → `ffmpeg`/`ffprobe`. Raw files land in `.screencast-runs/<id>/capture.mp4`.

## New and removed

Added the package, tests, and README expansion on 2026-09-24. No files removed.
