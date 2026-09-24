"""Session rules with a fake ffmpeg process. No X11 display."""

import io
import json
import unittest
from pathlib import Path

from screencast_mcp.session import ScreencastSession, ToolFailure


class FakeProcess:
    def __init__(self, returncode=None):
        self.returncode = returncode
        self.stdin = io.BytesIO()
        self.stderr = io.BytesIO(b"")
        self.signals = []

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        del timeout
        self.returncode = 0
        return 0

    def send_signal(self, sig):
        self.signals.append(sig)
        self.returncode = 0

    def terminate(self):
        self.returncode = 0

    def kill(self):
        self.returncode = 0


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.processes = []
        self.commands = []
        self.root = Path(self.id().replace(".", "_"))
        # unittest id is not a path; use tmp via TemporaryDirectory in each test if needed
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name)

    def _popen(self, command, **kwargs):
        del kwargs
        self.commands.append(command)
        process = FakeProcess()
        raw = Path(command[-1])
        raw.parent.mkdir(parents=True, exist_ok=True)
        raw.write_bytes(b"\x00" * 32)
        self.processes.append(process)
        return process

    def _run(self, command, **kwargs):
        del kwargs
        self.commands.append(list(command))
        out = Path(command[-1])
        if out.suffix in (".gif", ".mp4") and "capture.mp4" not in out.name:
            out.write_bytes(b"GIF89a-fake" if out.suffix == ".gif" else b"mp4-fake")

        class Completed:
            returncode = 0
            stdout = json.dumps(
                {"streams": [{"width": 320, "height": 240, "duration": "1.5"}], "format": {"duration": "1.5"}}
            )
            stderr = ""

        return Completed()

    def _session(self, platform_name="linux"):
        return ScreencastSession(
            popen=self._popen,
            run=self._run,
            which=lambda name: f"/usr/bin/{name}",
            platform_name=platform_name,
            environ={"DISPLAY": ":31"},
            detect_size=lambda display: (640, 480),
            clock=lambda: 100.0,
            sleep=lambda seconds: None,
        )

    def test_non_linux_is_a_clear_error(self):
        session = self._session(platform_name="darwin")
        with self.assertRaises(ToolFailure) as caught:
            session.start({})
        self.assertIn("Linux", caught.exception.payload["error"])
        self.assertFalse(self.commands)

    def test_start_stop_save_and_reject_stacking(self):
        session = self._session()
        started = session.start({"max_seconds": 5, "display": ":31", "out_dir": str(self.out)})
        self.assertTrue(started["ok"])
        self.assertEqual(started["display"], ":31")
        self.assertEqual(started["max_seconds"], 5)
        self.assertTrue(started["raw_path"].endswith("capture.mp4"))
        with self.assertRaises(ToolFailure) as caught:
            session.start({"max_seconds": 5})
        self.assertIn("already active", caught.exception.payload["error"])
        stopped = session.stop({})
        self.assertEqual(stopped["recording_id"], started["recording_id"])
        self.assertIn(b"q\n", self.processes[0].stdin.getvalue())
        saved = session.save({"format": "gif", "fps": 10, "scale_width": 320, "bezel": "phone"})
        self.assertTrue(saved["ok"])
        self.assertEqual(saved["format"], "gif")
        self.assertGreater(saved["bytes"], 0)
        self.assertEqual(saved["width"], 320)
        status = session.status({})
        self.assertEqual(status["state"], "idle")
        self.assertEqual(status["last"]["saves"]["gif"], saved["path"])

    def test_stop_without_recording(self):
        with self.assertRaises(ToolFailure) as caught:
            self._session().stop({})
        self.assertEqual(caught.exception.payload["error"], "no active recording")

    def test_max_seconds_hard_cap(self):
        session = self._session()
        with self.assertRaises(ToolFailure):
            session.start({"max_seconds": 61})
        with self.assertRaises(ToolFailure):
            session.start({"max_seconds": 0})


if __name__ == "__main__":
    unittest.main()
