import unittest
from pathlib import Path


class GovernanceCiConfigTests(unittest.TestCase):
    def test_cross_platform_lock_workflow_is_pinned_and_bounded(self):
        workflow_path = Path(".github/workflows/governance-locks.yml")
        self.assertTrue(workflow_path.is_file(), "governance lock workflow is missing")
        workflow = workflow_path.read_text(encoding="utf-8")

        self.assertIn("ubuntu-latest", workflow)
        self.assertIn("windows-latest", workflow)
        self.assertIn("actions/checkout@v7", workflow)
        self.assertIn("actions/setup-python@v7", workflow)
        self.assertIn("python-version: \"3.12\"", workflow)
        self.assertIn("contents: read", workflow)
        self.assertIn("timeout-minutes: 10", workflow)
        self.assertIn("tests.test_cross_platform_locking", workflow)
        self.assertNotIn("patchright install", workflow)
        self.assertNotIn("pip install", workflow)


if __name__ == "__main__":
    unittest.main()
