"""Real X11 capture. Skips unless DISPLAY and ffmpeg x11grab work."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from screencast_mcp.protocol import McpServer


def _grab_works() -> bool:
    display = os.environ.get("DISPLAY")
    if not display or not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        return False
    if not sys_platform_linux():
        return False
    probe = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "x11grab",
            "-video_size",
            "64x64",
            "-framerate",
            "5",
            "-i",
            display,
            "-t",
            "0.2",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        timeout=15,
        check=False,
    )
    return probe.returncode == 0


def sys_platform_linux() -> bool:
    import sys

    return sys.platform.startswith("linux")


@unittest.skipUnless(_grab_works(), "DISPLAY and ffmpeg x11grab are required")
class LiveCaptureTests(unittest.TestCase):
    def test_start_stop_save_gif_and_mp4(self):
        with tempfile.TemporaryDirectory(prefix="screencast-live-") as tmp:
            server = McpServer()
            server.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {"protocolVersion": "2025-06-18", "capabilities": {}},
                }
            )

            def call(msg_id, name, arguments):
                response = server.handle(
                    {
                        "jsonrpc": "2.0",
                        "id": msg_id,
                        "method": "tools/call",
                        "params": {"name": name, "arguments": arguments},
                    }
                )
                self.assertNotIn("error", response)
                payload = json.loads(response["result"]["content"][0]["text"])
                self.assertTrue(payload.get("ok"), payload)
                self.assertFalse(response["result"]["isError"])
                return payload

            started = call(
                2,
                "screencast_start",
                {"max_seconds": 2, "out_dir": tmp, "display": os.environ["DISPLAY"]},
            )
            stopped = call(3, "screencast_stop", {})
            self.assertEqual(stopped["recording_id"], started["recording_id"])
            self.assertGreater(stopped["duration_seconds"], 0)
            raw = Path(stopped["raw_path"])
            self.assertTrue(raw.is_file())
            self.assertGreater(raw.stat().st_size, 0)

            for fmt in ("gif", "mp4"):
                saved = call(
                    4 if fmt == "gif" else 5,
                    "screencast_save",
                    {"format": fmt, "recording_id": started["recording_id"], "scale_width": 320, "bezel": "desktop" if fmt == "mp4" else "phone"},
                )
                path = Path(saved["path"])
                self.assertTrue(path.is_file(), saved)
                self.assertGreater(saved["bytes"], 100)
                self.assertEqual(saved["format"], fmt)
                self.assertGreater(saved["width"], 0)
                self.assertGreater(saved["height"], 0)
                self.assertLessEqual(saved["duration_seconds"], 60)


if __name__ == "__main__":
    unittest.main()
