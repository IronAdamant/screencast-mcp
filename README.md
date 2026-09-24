# screencast-mcp

Small stdio MCP server that records an X11 viewport continuously (hard cap **60 seconds**) and saves a **GIF** or **MP4**. It is meant for agents that click through a UI with other desktop tools and need one smooth clip instead of a pile of screenshots.

The server process uses **Python 3 standard library only**. It shells out to `ffmpeg` and `ffprobe`, which must already be on `PATH`. Nothing is published to npm, and the server does not make network calls.

## Honesty

This records **whatever is on the X11 display**: windows, notifications, passwords, tokens, and private messages. Do not start a capture while secrets are visible. v1 is Linux/X11 only. macOS and Windows return a clear error.

`bezel` is best-effort framing, not a device mock:

- `none` — no extra crop or pad
- `phone` — center-crop toward a portrait 9:16 frame
- `desktop` — thin dark letterbox (32px)

## Requirements

- Python 3.9+
- Linux with an X11 `DISPLAY` (for example `:31`)
- `ffmpeg` and `ffprobe` on `PATH` (`x11grab` and `libx264`)
- `xdpyinfo` is optional; without it the capture size falls back to 1920x1080

There is no `requirements.txt`. Do not install `@modelcontextprotocol/sdk` or the PyPI `mcp` package for this server.

## Install

Clone the repo and point Cursor (or another MCP client) at `server.py`.

```json
{
  "mcpServers": {
    "screencast": {
      "command": "python3",
      "args": ["/absolute/path/to/server.py"]
    }
  }
}
```

From a clone, `args` is the absolute path of `server.py` in that clone. The process speaks MCP JSON-RPC on stdin/stdout. Logs, if any, go to stderr.

## Tools

| Tool | Role |
| --- | --- |
| `screencast_start` | Begin one capture. Errors if one is already running. |
| `screencast_stop` | Stop the active capture early. Errors if none is active. |
| `screencast_save` | Encode the raw capture to `gif` or `mp4`. |
| `screencast_status` | `idle` or `recording`, plus the last paths. |

`screencast_start` arguments:

- `max_seconds` (number, default 60, must be > 0 and ≤ 60)
- `display` (string, default `$DISPLAY`)
- `out_dir` (string, default `./.screencast-runs`, or the OS temp dir if that is not writable)

Returns `{ ok, recording_id, started_at, max_seconds, display, raw_path }`.

ffmpeg is started with `-t max_seconds`, so the capture also stops itself at the cap. After that, `screencast_stop` reports that nothing is active; use `screencast_status` and `screencast_save`.

`screencast_save` arguments:

- `format` (`gif` or `mp4`, required)
- `recording_id` (latest capture if omitted)
- `out_path`
- `fps` (GIF default 12, range 1–30)
- `scale_width`
- `bezel` (`none`, `phone`, or `desktop`)

Returns `{ ok, format, path, bytes, duration_seconds, width, height }`.

Call `screencast_stop` before `screencast_save` if the capture is still running. The raw file is a fragmented H.264 MP4 so an early stop still encodes.

Tool results are MCP `content` text blocks containing that JSON. Failures set `isError: true` and `{"ok": false, "error": "..."}`.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

Protocol and session tests do not need a display. A live capture (about 2 seconds, then GIF and MP4) runs only when `DISPLAY` is set and `ffmpeg` can grab it:

```bash
python3 -m unittest tests.test_live_capture -v
```

## Out of scope

- Auspex / Solari integration
- Clicking or driving the UI (use other desktop tools while this records)
- Publishing to npm
- macOS and Windows capture
