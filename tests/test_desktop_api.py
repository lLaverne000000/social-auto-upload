import asyncio
import importlib
import json
import os
import runpy
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import sau_desktop_api
from sau_desktop_api import create_desktop_app
from sau_desktop_service import JobStatus
from sau_runtime import RuntimePaths, get_runtime_paths


class DesktopApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        with patch.dict(os.environ, {"SOCIAL_AUTO_UPLOAD_HOME": self.temp.name}):
            self.paths = get_runtime_paths()
        self.jobs = Mock()
        self.app = create_desktop_app(
            paths=self.paths,
            session_token="secret",
            jobs=self.jobs,
        )
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp.cleanup()

    def _authorize(self, client=None):
        target = client or self.client
        target.set_cookie("sau_session", "secret")
        return {"Origin": "http://localhost"}

    def _publish_payload(self):
        media = Path(self.temp.name) / "demo.mp4"
        media.write_bytes(b"video")
        return {
            "platform": "xiaohongshu",
            "accountName": "creator",
            "mediaFile": str(media),
            "title": "标题",
            "tags": ["旅行"],
            "contentSource": "original",
        }

    def test_import_does_not_bind_a_socket(self):
        with patch("socket.socket.bind", side_effect=AssertionError("socket opened")):
            importlib.reload(sau_desktop_api)

    def test_index_sets_strict_http_only_session_cookie(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        header = response.headers["Set-Cookie"]
        self.assertIn("HttpOnly", header)
        self.assertIn("SameSite=Strict", header)
        self.assertIn("Path=/", header)

    def test_api_uses_two_gibibyte_request_limit_and_no_wildcard_cors(self):
        response = self.client.get("/api/v1/health")

        self.assertEqual(self.app.config["MAX_CONTENT_LENGTH"], 2 * 1024**3)
        self.assertIsNone(response.headers.get("Access-Control-Allow-Origin"))
        self.assertEqual(
            response.get_json(),
            {"ok": True, "data": {"status": "ok"}, "error": None},
        )

    def test_api_method_errors_keep_the_structured_envelope(self):
        response = self.client.delete(
            "/api/v1/health",
            headers=self._authorize(),
        )

        self.assertEqual(response.status_code, 405)
        self.assertEqual(set(response.get_json()), {"ok", "data", "error"})
        self.assertFalse(response.get_json()["ok"])

    def test_mutation_without_cookie_is_forbidden(self):
        response = self.client.post(
            "/api/v1/publish",
            json=self._publish_payload(),
            headers={"Origin": "http://localhost"},
        )

        self.assertEqual(response.status_code, 403)
        self.jobs.submit.assert_not_called()

    def test_mutation_requires_a_present_same_origin_header(self):
        self.client.set_cookie("sau_session", "secret")

        missing = self.client.post("/api/v1/publish", json=self._publish_payload())
        foreign = self.client.post(
            "/api/v1/publish",
            json=self._publish_payload(),
            headers={"Origin": "https://attacker.example"},
        )

        self.assertEqual(missing.status_code, 403)
        self.assertEqual(foreign.status_code, 403)
        self.jobs.submit.assert_not_called()

    def test_publish_with_cookie_and_origin_calls_job_manager(self):
        self.jobs.submit.return_value = SimpleNamespace(
            id="job-1",
            status=JobStatus.QUEUED,
            message="",
            result_code=None,
            created_at=1.0,
            updated_at=1.0,
        )

        response = self.client.post(
            "/api/v1/publish",
            json=self._publish_payload(),
            headers=self._authorize(),
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.get_json()["data"]["id"], "job-1")
        request_model = self.jobs.submit.call_args.args[0]
        self.assertEqual(request_model.platform, "xiaohongshu")
        self.assertEqual(request_model.account_name, "creator")
        self.assertFalse(request_model.automatic_publish)

    def test_publish_contract_accepts_minimal_job_snapshot(self):
        self.jobs.submit.return_value = Mock(
            id="job-minimal",
            status=Mock(value="queued"),
        )

        response = self.client.post(
            "/api/v1/publish",
            json=self._publish_payload(),
            headers=self._authorize(),
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.get_json()["data"]["id"], "job-minimal")
        self.assertEqual(response.get_json()["data"]["status"], "queued")

    def test_relative_managed_media_path_cannot_escape_media_directory(self):
        payload = self._publish_payload()
        payload["mediaFile"] = "../outside.mp4"

        response = self.client.post(
            "/api/v1/publish",
            json=payload,
            headers=self._authorize(),
        )

        self.assertEqual(response.status_code, 400)
        self.jobs.submit.assert_not_called()

    def test_account_name_cannot_be_used_as_a_cookie_path(self):
        payload = self._publish_payload()
        payload["accountName"] = "../../escape"

        response = self.client.post(
            "/api/v1/publish",
            json=payload,
            headers=self._authorize(),
        )

        self.assertEqual(response.status_code, 400)
        self.jobs.submit.assert_not_called()

    def test_account_name_cannot_smuggle_query_or_token_text(self):
        payload = self._publish_payload()
        payload["accountName"] = "creator?token=account-secret"

        response = self.client.post(
            "/api/v1/publish",
            json=payload,
            headers=self._authorize(),
        )

        self.assertEqual(response.status_code, 400)
        self.assertNotIn("account-secret", response.get_data(as_text=True))
        self.jobs.submit.assert_not_called()

    def test_get_job_sanitizes_sensitive_failure_details(self):
        sensitive = (
            f"failed at {self.paths.data_root}/cookies/account.json "
            "https://example.test/login?token=query-secret Cookie: cookie-secret"
        )
        self.jobs.get.return_value = SimpleNamespace(
            id="job-1",
            status=JobStatus.FAILED,
            message=sensitive,
            result_code=None,
            created_at=1.0,
            updated_at=2.0,
        )

        response = self.client.get("/api/v1/jobs/job-1")
        body = json.dumps(response.get_json(), ensure_ascii=False)

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("query-secret", body)
        self.assertNotIn("cookie-secret", body)
        self.assertNotIn(str(self.paths.data_root), body)
        self.assertNotIn("Traceback", body)

    def test_boundary_exception_is_not_returned_verbatim(self):
        self.jobs.submit.side_effect = RuntimeError(
            f"Traceback token=raw-secret at {self.paths.database_file}"
        )

        response = self.client.post(
            "/api/v1/publish",
            json=self._publish_payload(),
            headers=self._authorize(),
        )
        body = json.dumps(response.get_json(), ensure_ascii=False)

        self.assertEqual(response.status_code, 500)
        self.assertNotIn("raw-secret", body)
        self.assertNotIn(str(self.paths.database_file), body)
        self.assertNotIn("Traceback", body)

    def test_confirm_calls_only_job_manager_confirm(self):
        self.jobs.confirm.return_value = SimpleNamespace(
            id="job-1",
            status=JobStatus.RUNNING,
            message="",
            result_code=None,
            created_at=1.0,
            updated_at=2.0,
        )

        response = self.client.post(
            "/api/v1/jobs/job-1/confirm",
            headers=self._authorize(),
        )

        self.assertEqual(response.status_code, 200)
        self.jobs.confirm.assert_called_once_with("job-1")
        self.jobs.submit.assert_not_called()

    def test_login_uses_real_cli_parser_contract_and_reports_events(self):
        seen = []
        finished = threading.Event()

        async def dispatcher(args):
            seen.append((args.platform, args.action, args.account, args.headless))
            await asyncio.sleep(0)
            finished.set()
            return 0

        app = create_desktop_app(
            paths=self.paths,
            session_token="secret",
            jobs=self.jobs,
            login_dispatcher=dispatcher,
        )
        client = app.test_client()

        response = client.post(
            "/api/v1/login",
            json={"platform": "douyin", "accountName": "creator"},
            headers=self._authorize(client),
        )

        self.assertEqual(response.status_code, 202)
        job_id = response.get_json()["data"]["id"]
        self.assertTrue(finished.wait(2))
        events = client.get(f"/api/v1/login/{job_id}/events")
        self.assertEqual(events.status_code, 200)
        self.assertIn(b'"status": "succeeded"', events.data)
        self.assertEqual(seen, [("douyin", "login", "creator", False)])

    def test_login_rejects_unsupported_platform_without_fake_success(self):
        response = self.client.post(
            "/api/v1/login",
            json={"platform": "youtube", "accountName": "creator"},
            headers=self._authorize(),
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"]["code"], "unsupported")

    def test_safety_status_omits_local_paths(self):
        def reader(**_kwargs):
            return {
                "platform": "douyin",
                "account": "douyin_creator",
                "account_file": str(self.paths.cookies_dir / "douyin_creator.json"),
                "state_path": str(self.paths.safety_dir / "state.json"),
                "state_status": "missing",
                "lock": {"path": str(self.paths.safety_dir / "douyin.lock"), "exists": False},
                "audit": {"path": str(self.paths.safety_dir / "audit.jsonl"), "size_bytes": 0},
                "latest_failure": None,
            }

        app = create_desktop_app(
            paths=self.paths,
            session_token="secret",
            jobs=self.jobs,
            safety_reader=reader,
        )
        response = app.test_client().get(
            "/api/v1/safety/status?platform=douyin&account=creator"
        )
        body = json.dumps(response.get_json(), ensure_ascii=False)

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(str(self.paths.data_root), body)
        self.assertNotIn("account_file", body)
        self.assertNotIn("state_path", body)

    def test_database_tables_are_created_idempotently(self):
        create_desktop_app(paths=self.paths, session_token="second", jobs=self.jobs)

        with sqlite3.connect(self.paths.database_file) as connection:
            names = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }

        self.assertIn("user_info", names)
        self.assertIn("file_records", names)

    def test_injected_runtime_paths_must_stay_within_data_root(self):
        outside = Path(self.temp.name).parent / "outside-media"
        invalid = RuntimePaths(
            resource_root=self.paths.resource_root,
            data_root=self.paths.data_root,
            cookies_dir=self.paths.cookies_dir,
            profiles_dir=self.paths.profiles_dir,
            logs_dir=self.paths.logs_dir,
            safety_dir=self.paths.safety_dir,
            media_dir=outside,
            database_file=self.paths.database_file,
        )

        with self.assertRaisesRegex(ValueError, "media"):
            create_desktop_app(paths=invalid, session_token="secret", jobs=self.jobs)

    def test_compatibility_entry_point_has_no_wildcard_cors_and_retires_douyin(self):
        import sau_backend

        with patch("socket.socket.bind", side_effect=AssertionError("socket opened")):
            module = importlib.reload(sau_backend)
        client = module.app.test_client()

        response = client.post("/postVideo", json={"type": 3})
        health = client.get("/api/v1/health", headers={"Origin": "https://foreign.test"})

        self.assertEqual(response.status_code, 410)
        self.assertEqual(health.status_code, 200)
        self.assertIsNone(health.headers.get("Access-Control-Allow-Origin"))

    def test_source_compatibility_entry_point_binds_loopback_only(self):
        run_calls = []

        with patch("flask.Flask.run", side_effect=lambda *args, **kwargs: run_calls.append(kwargs)):
            runpy.run_path(str(Path(__file__).parents[1] / "sau_backend.py"), run_name="__main__")

        self.assertEqual(run_calls, [{"host": "127.0.0.1", "port": 5409}])


if __name__ == "__main__":
    unittest.main()
