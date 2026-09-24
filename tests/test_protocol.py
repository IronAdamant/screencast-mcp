"""JSON-RPC schema and stdio framing tests. No display and no ffmpeg."""

import io
import json
import unittest

from screencast_mcp.protocol import (
    McpServer,
    TOOLS,
    read_message,
    serve,
    tool_result,
    write_message,
)
from screencast_mcp.session import ToolFailure


class SchemaTests(unittest.TestCase):
    def test_tool_names_and_save_requires_format(self):
        names = [tool["name"] for tool in TOOLS]
        self.assertEqual(
            names,
            ["screencast_start", "screencast_stop", "screencast_save", "screencast_status"],
        )
        save = next(tool for tool in TOOLS if tool["name"] == "screencast_save")
        self.assertEqual(save["inputSchema"]["required"], ["format"])
        self.assertEqual(save["inputSchema"]["properties"]["format"]["enum"], ["gif", "mp4"])
        start = next(tool for tool in TOOLS if tool["name"] == "screencast_start")
        self.assertIn("max_seconds", start["inputSchema"]["properties"])
        self.assertNotIn("required", start["inputSchema"])

    def test_tool_result_is_single_line_json_text(self):
        result = tool_result({"ok": True, "recording_id": "abc"})
        self.assertFalse(result["isError"])
        text = result["content"][0]["text"]
        self.assertNotIn("\n", text)
        self.assertEqual(json.loads(text)["ok"], True)


class FramingTests(unittest.TestCase):
    def test_ndjson_round_trip(self):
        buffer = io.BytesIO()
        write_message(buffer, {"jsonrpc": "2.0", "id": 1, "method": "ping"})
        buffer.seek(0)
        self.assertEqual(read_message(buffer)["method"], "ping")

    def test_content_length_framing(self):
        body = b'{"jsonrpc":"2.0","id":7,"method":"ping"}'
        raw = b"Content-Length: %d\r\n\r\n%s" % (len(body), body)
        message = read_message(io.BytesIO(raw))
        self.assertEqual(message["id"], 7)


class _Boom:
    def status(self, arguments):
        del arguments
        return {"ok": True, "state": "idle", "recording_id": None, "last": None}

    def start(self, arguments):
        del arguments
        raise ToolFailure("a recording is already active", recording_id="abc")

    def stop(self, arguments):
        del arguments
        raise ToolFailure("no active recording")

    def save(self, arguments):
        del arguments
        raise ToolFailure("format is required and must be 'gif' or 'mp4'")


class DispatchTests(unittest.TestCase):
    def _server(self):
        return McpServer(session=_Boom())

    def test_initialize_and_tools_list(self):
        server = self._server()
        init = server.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "0"},
                },
            }
        )
        self.assertEqual(init["result"]["protocolVersion"], "2025-06-18")
        self.assertIn("tools", init["result"]["capabilities"])
        listed = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        self.assertEqual(len(listed["result"]["tools"]), 4)

    def test_notification_has_no_response(self):
        server = self._server()
        server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        self.assertIsNone(
            server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
        )

    def test_unknown_tool_is_jsonrpc_error(self):
        server = self._server()
        server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        response = server.handle(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "nope", "arguments": {}},
            }
        )
        self.assertEqual(response["error"]["code"], -32601)

    def test_duplicate_start_is_error_json(self):
        server = self._server()
        server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        response = server.handle(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "screencast_start", "arguments": {"max_seconds": 10}},
            }
        )
        self.assertTrue(response["result"]["isError"])
        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertFalse(payload["ok"])
        self.assertIn("already active", payload["error"])

    def test_stdio_smoke_status(self):
        stdin = io.BytesIO()
        stdout = io.BytesIO()
        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "screencast_status", "arguments": {}}},
        ]
        stdin.write("".join(json.dumps(message) + "\n" for message in messages).encode())
        stdin.seek(0)
        serve(stdin, stdout, McpServer(session=_Boom()))
        lines = [json.loads(line) for line in stdout.getvalue().splitlines() if line.strip()]
        self.assertEqual([line["id"] for line in lines], [1, 2, 3])
        self.assertEqual(lines[0]["result"]["protocolVersion"], "2024-11-05")
        self.assertEqual(len(lines[1]["result"]["tools"]), 4)
        status = json.loads(lines[2]["result"]["content"][0]["text"])
        self.assertEqual(status["state"], "idle")


if __name__ == "__main__":
    unittest.main()
