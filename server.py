#!/usr/bin/env python3
"""stdio MCP entrypoint. Logs go to stderr. stdout is JSON-RPC only."""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from screencast_mcp.protocol import serve  # noqa: E402


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] in ("-h", "--help"):
        sys.stderr.write(
            "screencast-mcp: stdio MCP server. "
            "Add it to Cursor with command python3 and args pointing at this file.\n"
        )
        return 0
    try:
        serve(sys.stdin.buffer, sys.stdout.buffer)
    except BrokenPipeError:
        return 0
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
