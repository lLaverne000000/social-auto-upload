import inspect
import os
import unittest
from unittest.mock import patch

import uploader.youtube_uploader.main as youtube_main


class YouTubeBrowserLaunchTests(unittest.TestCase):
    def test_authoritative_environment_executable_wins_over_local_setting(self):
        with (
            patch.object(youtube_main, "LOCAL_CHROME_PATH", "/configured/chrome"),
            patch.dict(
                os.environ,
                {"SAU_CHROMIUM_EXECUTABLE": "/verified/chromium"},
                clear=True,
            ),
        ):
            kwargs = youtube_main._build_chromium_launch_kwargs(headless=True)

        self.assertEqual(kwargs["executable_path"], "/verified/chromium")
        self.assertNotIn("channel", kwargs)
        self.assertTrue(kwargs["headless"])

    def test_source_mode_without_configured_executable_keeps_system_channel(self):
        with (
            patch.object(youtube_main, "LOCAL_CHROME_PATH", ""),
            patch.dict(os.environ, {}, clear=True),
        ):
            kwargs = youtube_main._build_chromium_launch_kwargs(headless=False)

        self.assertEqual(kwargs["channel"], "chrome")
        self.assertNotIn("executable_path", kwargs)
        self.assertFalse(kwargs["headless"])

    def test_all_youtube_launch_sites_use_the_shared_resolver(self):
        source = inspect.getsource(youtube_main)
        self.assertNotIn("playwright.chromium.launch(", source)
        self.assertEqual(source.count("await _launch_chromium("), 3)
