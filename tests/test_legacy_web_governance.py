import unittest

try:
    from sau_backend import app
except ModuleNotFoundError:
    app = None


@unittest.skipIf(app is None, "web optional dependencies are not installed")
class LegacyWebGovernanceTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        self.payload = {
            "fileList": ["demo.mp4"],
            "accountList": ["account.json"],
            "type": 1,
            "title": "test",
            "tags": [],
        }

    def test_single_legacy_xhs_publish_returns_gone(self):
        response = self.client.post("/postVideo", json=self.payload)
        self.assertEqual(response.status_code, 410)

    def test_batch_legacy_xhs_publish_returns_gone(self):
        response = self.client.post("/postVideoBatch", json=[self.payload])
        self.assertEqual(response.status_code, 410)


if __name__ == "__main__":
    unittest.main()
