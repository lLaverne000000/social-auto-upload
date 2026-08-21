from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class LogConfigurationTests(unittest.TestCase):
    def test_windowed_process_without_standard_streams_imports_logging(self):
        project_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            environment = os.environ.copy()
            environment["SOCIAL_AUTO_UPLOAD_HOME"] = temp
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    (
                        "import sys; "
                        "sys.stdout = None; sys.stderr = None; "
                        "import utils.log; "
                        "utils.log.xiaohongshu_logger.info('windowed-log-ok')"
                    ),
                ],
                cwd=project_root,
                env=environment,
                capture_output=True,
                text=True,
                timeout=15,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            log_file = Path(temp) / "logs" / "xiaohongshu.log"
            self.assertTrue(log_file.is_file())
            self.assertIn("windowed-log-ok", log_file.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
