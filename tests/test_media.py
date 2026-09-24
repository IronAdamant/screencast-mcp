"""Unit tests for ffmpeg argument builders. No display required."""

import unittest

from screencast_mcp.media import (
    build_capture_command,
    build_encode_command,
    build_video_filter,
    validate_display,
)


class MediaTests(unittest.TestCase):
    def test_display_accepts_x11_names(self):
        self.assertEqual(validate_display(":31"), ":31")
        self.assertEqual(validate_display(":0.0"), ":0.0")
        self.assertEqual(validate_display("localhost:1"), "localhost:1")

    def test_display_rejects_shell_metacharacters(self):
        for bad in ("", ":31;rm", "wayland-0", "-f", ":31\n", "DISPLAY=:0"):
            with self.assertRaises(ValueError):
                validate_display(bad)

    def test_capture_command_is_argv_not_a_shell_string(self):
        command = build_capture_command("/usr/bin/ffmpeg", ":31", 1921, 1080, 60, "/tmp/raw.mp4")
        self.assertEqual(command[0], "/usr/bin/ffmpeg")
        self.assertIn("x11grab", command)
        self.assertIn(":31", command)
        self.assertIn("1920x1080", command)
        self.assertIn("+frag_keyframe+empty_moov+default_base_moof", command)
        self.assertEqual(command[command.index("-t") + 1], "60.000")
        self.assertNotIn(";", " ".join(command))

    def test_gif_filter_uses_palette_and_default_fps(self):
        video_filter = build_video_filter("gif", None, 480, "none")
        self.assertIn("fps=12.000", video_filter)
        self.assertIn("scale=480:-2:flags=lanczos", video_filter)
        self.assertIn("palettegen", video_filter)

    def test_phone_and_desktop_bezels(self):
        phone = build_video_filter("mp4", None, None, "phone")
        desktop = build_video_filter("mp4", None, None, "desktop")
        self.assertIn("crop=", phone)
        self.assertIn("9/16", phone)
        self.assertIn("pad=", desktop)
        self.assertIsNone(build_video_filter("mp4", None, None, "none"))

    def test_encode_command_caps_at_60_seconds(self):
        command = build_encode_command(
            "ffmpeg", "raw.mp4", "out.mp4", "mp4", None, None, "none"
        )
        self.assertEqual(command[command.index("-t") + 1], "60")
        self.assertTrue(command[-1].endswith("out.mp4"))
        gif = build_encode_command("ffmpeg", "raw.mp4", "out.gif", "gif", 10, None, "phone")
        self.assertIn("paletteuse", " ".join(gif))
        self.assertTrue(gif[-1].endswith("out.gif"))


if __name__ == "__main__":
    unittest.main()
