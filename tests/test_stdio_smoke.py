"""Spawn server.py and exercise initialize, tools/list, and status over stdio."""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "server.py"


class StdioSmokeTests(unittest.TestCase):
    def test_server_lists_tools_and_reports_idle(self):
        messages = [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "smoke", "version": "0"},
                },
            },
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "screencast_status", "arguments": {}},
            },
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "screencast_stop", "arguments": {}},
            },
        ]
        payload = "".join(json.dumps(message) + "\n" for message in messages).encode()
        env = os.environ.copy()
        env.pop("DISPLAY", None)
        completed = subprocess.run(
            [sys.executable, str(SERVER)],
            input=payload,
            capture_output=True,
            cwd=ROOT,
            env=env,
            timeout=10,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode())
        lines = [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]
        by_id = {line["id"]: line for line in lines}
        names = [tool["name"] for tool in by_id[2]["result"]["tools"]]
        self.assertEqual(
            names,
            ["screencast_start", "screencast_stop", "screencast_save", "screencast_status"],
        )
        status = json.loads(by_id[3]["result"]["content"][0]["text"])
        self.assertEqual(status, {
            "ok": True,
            "state": "idle",
            "recording_id": None,
            "display": None,
            "started_at": None,
            "max_seconds": None,
            "raw_path": None,
            "last": None,
        })
        self.assertTrue(by_id[4]["result"]["isError"])
        self.assertIn("screencast-mcp", by_id[1]["result"]["serverInfo"]["name"])


if __name__ == "__main__":
    unittest.main()
