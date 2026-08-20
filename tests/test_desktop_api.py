import asyncio
import importlib
import io
import json
import os
import runpy
import sqlite3
import struct
import tempfile
import threading
import time
import unittest
import zlib
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
        self.app.config["SERVER_NAME"] = "127.0.0.1"
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp.cleanup()

    def _authorize(self, client=None):
        target = client or self.client
        target.set_cookie("sau_session", "secret", domain="127.0.0.1")
        return {"Origin": "http://127.0.0.1"}

    def _publish_payload(self):
        media = self.paths.media_dir / "demo.mp4"
        media.write_bytes(b"video")
        return {
            "platform": "xiaohongshu",
            "accountName": "creator",
            "mediaFile": str(media),
            "title": "标题",
            "tags": ["旅行"],
            "contentSource": "original",
        }

    @staticmethod
    def _mp4_bytes():
        def box(box_type, payload):
            return struct.pack(">I4s", len(payload) + 8, box_type) + payload

        ftyp = box(b"ftyp", b"isom\x00\x00\x00\x00isommp42")
        moov = box(b"moov", box(b"mvhd", b"\x00" * 20))
        mdat = box(b"mdat", b"\x00\x00\x01\x09")
        return ftyp + moov + mdat

    @staticmethod
    def _png_bytes():
        def chunk(chunk_type, payload):
            body = chunk_type + payload
            return (
                struct.pack(">I", len(payload))
                + body
                + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
            )

        ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
        return (
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00"))
            + chunk(b"IEND", b"")
        )

    @staticmethod
    def _jpeg_bytes():
        dqt = b"\x00" + bytes(range(1, 65))
        sof = b"\x08\x00\x01\x00\x01\x01\x01\x11\x00"
        sos = b"\x01\x01\x00\x00\x3f\x00"
        return (
            b"\xff\xd8"
            + b"\xff\xdb" + struct.pack(">H", len(dqt) + 2) + dqt
            + b"\xff\xc0" + struct.pack(">H", len(sof) + 2) + sof
            + b"\xff\xda" + struct.pack(">H", len(sos) + 2) + sos
            + b"\x00\xff\xd9"
        )

    @staticmethod
    def _gif_bytes():
        return bytes.fromhex(
            "47494638396101000100800000ffffff000000"
            "2c00000000010001000002024401003b"
        )

    @staticmethod
    def _webp_bytes():
        chunk = b"VP8L" + struct.pack("<I", 5) + b"\x2f\x00\x00\x00\x00" + b"\x00"
        return b"RIFF" + struct.pack("<I", len(chunk) + 4) + b"WEBP" + chunk

    @staticmethod
    def _webm_bytes():
        doc_type = b"\x42\x82\x84webm"
        ebml = b"\x1a\x45\xdf\xa3" + bytes([0x80 | len(doc_type)]) + doc_type
        tracks = b"\x16\x54\xae\x6b\x80"
        cluster = b"\x1f\x43\xb6\x75\x80"
        segment_body = tracks + cluster
        segment = (
            b"\x18\x53\x80\x67"
            + bytes([0x80 | len(segment_body)])
            + segment_body
        )
        return ebml + segment

    def test_import_does_not_bind_a_socket(self):
        with patch("socket.socket.bind", side_effect=AssertionError("socket opened")):
            importlib.reload(sau_desktop_api)

    def test_frontend_uses_only_same_origin_private_api_contract(self):
        frontend_root = Path("sau_frontend")
        request_source = (frontend_root / "src/utils/request.js").read_text(
            encoding="utf-8"
        )
        material_source = (frontend_root / "src/api/material.js").read_text(
            encoding="utf-8"
        )
        publish_source = (frontend_root / "src/views/PublishCenter.vue").read_text(
            encoding="utf-8"
        )
        all_sources = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted((frontend_root / "src").rglob("*"))
            if path.is_file()
        )

        self.assertIn("baseURL: '/'", request_source)
        self.assertIn("withCredentials: true", request_source)
        self.assertIn("'/api/v1/materials'", material_source)
        self.assertIn("'/api/v1/publish'", publish_source)
        self.assertIn("materialId:", publish_source)
        for retired in (
            "http://localhost:5409",
            "/postVideo",
            "/uploadSave",
            "/getFile",
            "/getFiles",
            "/deleteFile",
            "/uploadCookie",
            "/downloadCookie",
        ):
            self.assertNotIn(retired, all_sources)

    def test_frontend_publish_job_requires_terminal_poll_or_explicit_confirmation(self):
        publish_source = Path(
            "sau_frontend/src/views/PublishCenter.vue"
        ).read_text(encoding="utf-8")

        for status, message in (
            ("queued", "任务已排队"),
            ("running", "正在执行发布"),
            ("waiting-for-login", "等待账号登录"),
            ("waiting-for-confirmation", "等待你的发布确认"),
            ("succeeded", "发布成功"),
            ("failed", "发布失败"),
            ("blocked", "已被本地安全控制阻止"),
        ):
            self.assertIn(f"'{status}':", publish_source)
            self.assertIn(message, publish_source)
        self.assertIn("/api/v1/jobs/${job.id}", publish_source)
        self.assertIn("/api/v1/jobs/${job.id}/confirm", publish_source)
        self.assertIn("v-if=\"job.status === 'waiting-for-confirmation'\"", publish_source)
        self.assertIn("automaticPublish: form.automaticPublish", publish_source)
        self.assertIn("onBeforeUnmount(clearJobPoll)", publish_source)

    def test_frontend_login_uses_post_then_relative_status_event_stream(self):
        account_source = Path(
            "sau_frontend/src/views/AccountManagement.vue"
        ).read_text(encoding="utf-8")

        self.assertIn("http.post('/api/v1/login'", account_source)
        self.assertIn(
            "new EventSource(`/api/v1/login/${jobId}/events`)",
            account_source,
        )
        self.assertIn("addEventListener('status'", account_source)
        self.assertIn("onBeforeUnmount(closeSSEConnection)", account_source)
        self.assertNotIn("/login?", account_source)

    def test_frontend_keeps_persistent_cross_computer_warning(self):
        app_source = Path("sau_frontend/src/App.vue").read_text(encoding="utf-8")

        self.assertIn(
            "同一账号不要在多台电脑同时发布。本机发布锁不能协调其他电脑。",
            app_source,
        )
        self.assertIn('role="alert"', app_source)

    def test_frontend_runtime_helpers_and_polling_requests_are_explicit(self):
        publish_source = Path(
            "sau_frontend/src/views/PublishCenter.vue"
        ).read_text(encoding="utf-8")
        request_source = Path(
            "sau_frontend/src/utils/request.js"
        ).read_text(encoding="utf-8")

        self.assertIn("import { formatBytes }", publish_source)
        self.assertIn("createJobPoller", publish_source)
        self.assertIn("silent: true", publish_source)
        self.assertIn("error.config?.silent", request_source)
        self.assertIn("apiError", request_source)

    def test_frontend_tracking_failure_is_unknown_and_keeps_publish_locked(self):
        publish_source = Path(
            "sau_frontend/src/views/PublishCenter.vue"
        ).read_text(encoding="utf-8")
        poller_source = Path(
            "sau_frontend/src/utils/jobPolling.js"
        ).read_text(encoding="utf-8")

        self.assertIn("onTrackingUnavailable", poller_source)
        self.assertNotIn("onFailure", poller_source)
        self.assertIn("trackingUnavailable", publish_source)
        self.assertIn("重新查询任务状态", publish_source)
        self.assertIn(
            "无法确认结果，后台任务可能仍在执行，不要重复提交",
            publish_source,
        )
        self.assertNotIn("status: 'failed'", publish_source)
        callback = publish_source.split("onTrackingUnavailable:", 1)[1].split(
            "})", 1
        )[0]
        self.assertNotIn("status: 'failed'", callback)
        self.assertNotIn("publishing.value = false", callback)

    def test_frontend_partial_uploads_and_about_copy_match_available_features(self):
        material_source = Path(
            "sau_frontend/src/views/MaterialManagement.vue"
        ).read_text(encoding="utf-8")
        about_source = Path("sau_frontend/src/views/About.vue").read_text(
            encoding="utf-8"
        )

        self.assertIn("Promise.allSettled", material_source)
        self.assertIn("uploadFiles.value = failedUploads", material_source)
        self.assertIn("await fetchMaterials()", material_source)
        for retired_claim in ("一键多平台发布", "批量发布", "Cookie 导入导出"):
            self.assertNotIn(retired_claim, about_source)
        for available_feature in (
            "GUI 与 CLI",
            "本机素材库",
            "人工确认",
            "离线运行",
        ):
            self.assertIn(available_feature, about_source)

    def test_index_sets_strict_http_only_session_cookie(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        header = response.headers["Set-Cookie"]
        self.assertIn("HttpOnly", header)
        self.assertIn("SameSite=Strict", header)
        self.assertIn("Path=/", header)

    def test_untrusted_host_cannot_receive_cookie_or_forge_mutation(self):
        attacker = self.app.test_client()

        index = attacker.get("/", base_url="http://attacker.example")
        attacker.set_cookie("sau_session", "secret", domain="attacker.example")
        mutation = attacker.post(
            "/api/v1/publish",
            base_url="http://attacker.example",
            json=self._publish_payload(),
            headers={"Origin": "http://attacker.example"},
        )

        self.assertEqual(index.status_code, 403)
        self.assertNotIn("Set-Cookie", index.headers)
        self.assertEqual(mutation.status_code, 403)
        self.jobs.submit.assert_not_called()

    def test_literal_loopback_host_and_matching_origin_are_accepted(self):
        self.jobs.submit.return_value = SimpleNamespace(
            id="job-loopback",
            status=JobStatus.QUEUED,
            message="",
            result_code=None,
            created_at=1.0,
            updated_at=1.0,
        )

        index = self.client.get("/")
        mutation = self.client.post(
            "/api/v1/publish",
            json=self._publish_payload(),
            headers={"Origin": "http://127.0.0.1"},
        )

        self.assertEqual(index.status_code, 200)
        self.assertEqual(mutation.status_code, 202)

    def test_origin_port_must_match_validated_loopback_host(self):
        client = self.app.test_client()
        client.set_cookie("sau_session", "secret", domain="127.0.0.1")
        self.jobs.submit.return_value = SimpleNamespace(
            id="job-port",
            status=JobStatus.QUEUED,
            message="",
            result_code=None,
            created_at=1.0,
            updated_at=1.0,
        )

        wrong = client.post(
            "/api/v1/publish",
            base_url="http://127.0.0.1:5409",
            json=self._publish_payload(),
            headers={"Origin": "http://127.0.0.1:5410"},
        )
        correct = client.post(
            "/api/v1/publish",
            base_url="http://127.0.0.1:5409",
            json=self._publish_payload(),
            headers={"Origin": "http://127.0.0.1:5409"},
        )

        self.assertEqual(wrong.status_code, 403)
        self.assertEqual(correct.status_code, 202)

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
            headers={"Origin": "http://127.0.0.1"},
        )

        self.assertEqual(response.status_code, 403)
        self.jobs.submit.assert_not_called()

    def test_mutation_requires_a_present_same_origin_header(self):
        self.client.set_cookie("sau_session", "secret", domain="127.0.0.1")

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

    def test_absolute_media_file_outside_runtime_media_is_rejected(self):
        outside = self.paths.data_root / "outside.mp4"
        outside.write_bytes(b"video")
        payload = self._publish_payload()
        payload["mediaFile"] = str(outside)

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
        app.config["SERVER_NAME"] = "127.0.0.1"
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

    def test_login_dispatch_uses_injected_runtime_cookie_directory(self):
        from sau_cli import dispatch as cli_dispatch
        from sau_cli import resolve_account_file

        resolved = []
        finished = threading.Event()

        async def login_account(account_name, headless=True):
            resolved.append(resolve_account_file("douyin", account_name))
            finished.set()
            return {
                "success": True,
                "message": "completed",
                "account_file": str(resolved[-1]),
            }

        app = create_desktop_app(
            paths=self.paths,
            session_token="secret",
            jobs=self.jobs,
            login_dispatcher=cli_dispatch,
        )
        app.config["SERVER_NAME"] = "127.0.0.1"
        client = app.test_client()
        client.set_cookie("sau_session", "secret", domain="127.0.0.1")

        with patch("sau_cli.login_douyin_account", new=login_account):
            response = client.post(
                "/api/v1/login",
                json={"platform": "douyin", "accountName": "scoped"},
                headers={"Origin": "http://127.0.0.1"},
            )
            self.assertTrue(finished.wait(2))

        self.assertEqual(response.status_code, 202)
        self.assertEqual(resolved, [self.paths.cookies_dir / "douyin_scoped.json"])

    def test_login_executor_is_bounded_and_exposes_shutdown_hook(self):
        lock = threading.Lock()
        release = threading.Event()
        three_started = threading.Event()
        active = 0
        maximum = 0

        async def dispatcher(_args):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
                if active >= 3:
                    three_started.set()
            await asyncio.to_thread(release.wait, 2)
            with lock:
                active -= 1
            return 0

        app = create_desktop_app(
            paths=self.paths,
            session_token="secret",
            jobs=self.jobs,
            login_dispatcher=dispatcher,
        )
        app.config["SERVER_NAME"] = "127.0.0.1"
        client = app.test_client()
        client.set_cookie("sau_session", "secret", domain="127.0.0.1")
        try:
            for index in range(3):
                response = client.post(
                    "/api/v1/login",
                    json={"platform": "douyin", "accountName": f"creator-{index}"},
                    headers={"Origin": "http://127.0.0.1"},
                )
                self.assertEqual(response.status_code, 202)
            self.assertFalse(three_started.wait(0.5))
            self.assertLessEqual(maximum, 2)
            self.assertTrue(callable(app.extensions["sau_desktop_shutdown"]))
        finally:
            release.set()
        app.extensions["sau_desktop_shutdown"]()

    def test_login_shutdown_is_fast_and_cancels_queued_jobs(self):
        release = threading.Event()
        two_started = threading.Event()
        third_started = threading.Event()
        lock = threading.Lock()
        started = 0

        def dispatcher(_args):
            nonlocal started
            with lock:
                started += 1
                if started == 2:
                    two_started.set()
                if started == 3:
                    third_started.set()
            release.wait(0.5)
            return 0

        app = create_desktop_app(
            paths=self.paths,
            session_token="secret",
            jobs=self.jobs,
            login_dispatcher=dispatcher,
        )
        manager = app.extensions["sau_login_jobs"]
        submitted = [
            manager.submit("douyin", f"shutdown-{index}")
            for index in range(3)
        ]
        self.assertTrue(two_started.wait(1))

        started_at = time.monotonic()
        manager.shutdown()
        elapsed = time.monotonic() - started_at
        release.set()

        self.assertLess(elapsed, 0.2)
        self.assertFalse(third_started.is_set())
        self.assertEqual(
            {manager.get(job.id).status for job in submitted},
            {JobStatus.BLOCKED.value},
        )
        with self.assertRaises(RuntimeError):
            manager.submit("douyin", "after-shutdown")

    def test_login_shutdown_wins_before_worker_enters_dispatch(self):
        worker_ready = threading.Event()
        release_worker = threading.Event()
        worker_finished = threading.Event()
        dispatcher_started = threading.Event()

        def dispatcher(_args):
            dispatcher_started.set()
            return 0

        app = create_desktop_app(
            paths=self.paths,
            session_token="secret",
            jobs=self.jobs,
            login_dispatcher=dispatcher,
        )
        manager = app.extensions["sau_login_jobs"]
        original_run = manager._run

        def paused_run(job_id):
            worker_ready.set()
            release_worker.wait(1)
            try:
                original_run(job_id)
            finally:
                worker_finished.set()

        manager._run = paused_run
        submitted = manager.submit("douyin", "shutdown-window")
        self.assertTrue(worker_ready.wait(1))

        started_at = time.monotonic()
        manager.shutdown()
        elapsed = time.monotonic() - started_at
        release_worker.set()
        self.assertTrue(worker_finished.wait(1))
        self.assertLess(elapsed, 0.2)
        self.assertFalse(dispatcher_started.is_set())
        self.assertEqual(
            manager.get(submitted.id).status,
            JobStatus.BLOCKED.value,
        )

    def test_login_manager_retains_at_most_two_hundred_completed_jobs(self):
        app = create_desktop_app(
            paths=self.paths,
            session_token="secret",
            jobs=self.jobs,
            login_dispatcher=lambda _args: 0,
        )
        manager = app.extensions["sau_login_jobs"]
        try:
            submitted = []
            for index in range(201):
                job = manager.submit("douyin", f"retained-{index}")
                submitted.append(job)
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    if manager.get(job.id).status in {
                        JobStatus.SUCCEEDED.value,
                        JobStatus.FAILED.value,
                    }:
                        break
                    time.sleep(0.005)
                else:
                    self.fail("login job did not complete")
                time.sleep(0.005)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                try:
                    manager.get(submitted[0].id)
                except KeyError:
                    break
                time.sleep(0.005)
            else:
                self.fail("completed login jobs were not bounded")
            with self.assertRaises(KeyError):
                manager.get(submitted[0].id)
            self.assertEqual(
                manager.get(submitted[-1].id).status,
                JobStatus.SUCCEEDED.value,
            )
        finally:
            manager.shutdown()

    def test_duplicate_active_login_reuses_job_and_queue_is_bounded(self):
        release = threading.Event()

        async def dispatcher(_args):
            await asyncio.to_thread(release.wait, 2)
            return 0

        app = create_desktop_app(
            paths=self.paths,
            session_token="secret",
            jobs=self.jobs,
            login_dispatcher=dispatcher,
        )
        manager = app.extensions["sau_login_jobs"]
        try:
            first = manager.submit("douyin", "duplicate")
            duplicate = manager.submit("douyin", "duplicate")
            self.assertEqual(duplicate.id, first.id)
            for index in range(19):
                manager.submit("douyin", f"queued-{index}")
            with self.assertRaisesRegex(RuntimeError, "queue"):
                manager.submit("douyin", "overflow")
        finally:
            release.set()
            manager.shutdown()

    def test_static_symlinks_cannot_escape_frontend_root(self):
        resource_root = Path(self.temp.name) / "resources"
        frontend = resource_root / "frontend"
        assets = frontend / "assets"
        assets.mkdir(parents=True)
        outside_index = resource_root / "outside-index.html"
        outside_asset = resource_root / "outside-secret.txt"
        outside_index.write_text("INDEX-SECRET", encoding="utf-8")
        outside_asset.write_text("ASSET-SECRET", encoding="utf-8")
        (frontend / "index.html").symlink_to(outside_index)
        (assets / "leak.txt").symlink_to(outside_asset)
        paths = RuntimePaths(
            resource_root=resource_root,
            data_root=self.paths.data_root,
            cookies_dir=self.paths.cookies_dir,
            profiles_dir=self.paths.profiles_dir,
            logs_dir=self.paths.logs_dir,
            safety_dir=self.paths.safety_dir,
            media_dir=self.paths.media_dir,
            database_file=self.paths.database_file,
        )
        app = create_desktop_app(paths=paths, session_token="secret", jobs=self.jobs)
        app.config["SERVER_NAME"] = "127.0.0.1"
        client = app.test_client()

        index = client.get("/")
        asset = client.get("/assets/leak.txt")

        self.assertNotIn(b"INDEX-SECRET", index.data)
        self.assertNotIn(b"ASSET-SECRET", asset.data)
        self.assertEqual(asset.status_code, 404)

    def test_material_contract_uploads_lists_and_publishes_by_opaque_id(self):
        client = self.client
        headers = self._authorize(client)

        upload = client.post(
            "/api/v1/materials",
            data={"file": (io.BytesIO(self._mp4_bytes()), "demo.mp4")},
            content_type="multipart/form-data",
            headers=headers,
        )

        self.assertEqual(upload.status_code, 201)
        material = upload.get_json()["data"]
        self.assertRegex(material["id"], r"^[0-9a-f]{32}$")
        self.assertNotIn("path", json.dumps(material).lower())
        with sqlite3.connect(self.paths.database_file) as connection:
            stored_name = connection.execute(
                "SELECT stored_name FROM desktop_materials WHERE id = ?",
                (material["id"],),
            ).fetchone()[0]
        self.assertEqual(Path(stored_name).name, stored_name)
        self.assertTrue(stored_name.startswith(material["id"]))
        listing = client.get("/api/v1/materials")
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.get_json()["data"]["materials"], [material])
        preview = client.get(f"/api/v1/materials/{material['id']}/preview")
        download = client.get(f"/api/v1/materials/{material['id']}/download")
        self.assertEqual(preview.data, self._mp4_bytes())
        self.assertEqual(download.data, self._mp4_bytes())
        self.assertEqual(preview.headers["Content-Type"], "video/mp4")
        self.assertEqual(preview.headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("sandbox", preview.headers["Content-Security-Policy"])
        self.assertEqual(download.headers["Content-Type"], "application/octet-stream")
        self.assertIn("attachment", download.headers["Content-Disposition"])
        self.assertEqual(download.headers["X-Content-Type-Options"], "nosniff")
        preview.close()
        download.close()

        self.jobs.submit.return_value = SimpleNamespace(
            id="material-job",
            status=JobStatus.QUEUED,
            message="",
            result_code=None,
            created_at=1.0,
            updated_at=1.0,
        )
        publish = client.post(
            "/api/v1/publish",
            json={
                "platform": "xiaohongshu",
                "accountName": "creator",
                "materialId": material["id"],
                "title": "标题",
                "contentSource": "original",
            },
            headers=headers,
        )
        self.assertEqual(publish.status_code, 202)
        request_model = self.jobs.submit.call_args.args[0]
        self.assertEqual(request_model.media_file.parent, self.paths.media_dir)
        self.assertTrue(request_model.media_file.is_file())

    def test_material_reads_require_session_and_reject_symlink_escape(self):
        unauthenticated = self.app.test_client()
        response = unauthenticated.get("/api/v1/materials")
        self.assertEqual(response.status_code, 403)

        upload = self.client.post(
            "/api/v1/materials",
            data={"file": (io.BytesIO(self._mp4_bytes()), "safe.mp4")},
            content_type="multipart/form-data",
            headers=self._authorize(),
        )
        self.assertEqual(upload.status_code, 201)
        material_id = upload.get_json()["data"]["id"]
        with sqlite3.connect(self.paths.database_file) as connection:
            stored_name = connection.execute(
                "SELECT stored_name FROM desktop_materials WHERE id = ?",
                (material_id,),
            ).fetchone()[0]
        stored_file = self.paths.media_dir / stored_name
        stored_file.unlink()
        outside = self.paths.data_root / "outside-secret.txt"
        outside.write_text("MATERIAL-SECRET", encoding="utf-8")
        stored_file.symlink_to(outside)

        preview = self.client.get(f"/api/v1/materials/{material_id}/preview")

        self.assertEqual(preview.status_code, 404)
        self.assertNotIn(b"MATERIAL-SECRET", preview.data)

    def test_material_upload_rejects_active_and_disguised_content(self):
        headers = self._authorize()
        samples = (
            ("payload.html", b"<!doctype html><script>alert(1)</script>"),
            ("payload.svg", b"<svg onload='alert(1)' xmlns='http://www.w3.org/2000/svg'/>"),
            ("disguised.mp4", b"<!doctype html><script>alert(1)</script>"),
            ("disguised.png", b"<svg xmlns='http://www.w3.org/2000/svg'/>")
        )
        for filename, payload in samples:
            with self.subTest(filename=filename):
                response = self.client.post(
                    "/api/v1/materials",
                    data={"file": (io.BytesIO(payload), filename)},
                    content_type="multipart/form-data",
                    headers=headers,
                )
                self.assertEqual(response.status_code, 415)
        self.assertEqual(list(self.paths.media_dir.iterdir()), [])

    def test_material_upload_rejects_truncated_or_structurally_fake_media(self):
        samples = (
            ("truncated.jpg", b"\xff\xd8\xff"),
            ("truncated.png", b"\x89PNG\r\n\x1a\n"),
            ("truncated.gif", b"GIF89a"),
            ("truncated.webp", b"RIFF\x04\x00\x00\x00WEBP"),
            ("truncated.webm", b"\x1a\x45\xdf\xa3"),
            (
                "active.mp4",
                b"\x00\x00\x00\x18ftyp"
                b"<!doctype html><script>alert(1)</script>",
            ),
        )
        for filename, payload in samples:
            with self.subTest(filename=filename):
                response = self.client.post(
                    "/api/v1/materials",
                    data={"file": (io.BytesIO(payload), filename)},
                    content_type="multipart/form-data",
                    headers=self._authorize(),
                )
                self.assertEqual(response.status_code, 415)
        self.assertEqual(list(self.paths.media_dir.iterdir()), [])

    def test_material_upload_accepts_structurally_valid_media_fixtures(self):
        samples = (
            ("valid.mp4", self._mp4_bytes()),
            ("valid.png", self._png_bytes()),
            ("valid.jpg", self._jpeg_bytes()),
            ("valid.gif", self._gif_bytes()),
            ("valid.webp", self._webp_bytes()),
            ("valid.webm", self._webm_bytes()),
        )
        for filename, payload in samples:
            with self.subTest(filename=filename):
                response = self.client.post(
                    "/api/v1/materials",
                    data={"file": (io.BytesIO(payload), filename)},
                    content_type="multipart/form-data",
                    headers=self._authorize(),
                )
                self.assertEqual(response.status_code, 201)

    def test_material_delete_restores_file_on_database_failure(self):
        upload = self.client.post(
            "/api/v1/materials",
            data={"file": (io.BytesIO(self._mp4_bytes()), "delete.mp4")},
            content_type="multipart/form-data",
            headers=self._authorize(),
        )
        material_id = upload.get_json()["data"]["id"]
        with sqlite3.connect(self.paths.database_file) as connection:
            stored_name = connection.execute(
                "SELECT stored_name FROM desktop_materials WHERE id = ?",
                (material_id,),
            ).fetchone()[0]
            connection.execute("""
                CREATE TRIGGER fail_material_delete
                BEFORE DELETE ON desktop_materials
                BEGIN SELECT RAISE(ABORT, 'forced delete failure'); END
            """)
        material_file = self.paths.media_dir / stored_name

        failed = self.client.delete(
            f"/api/v1/materials/{material_id}",
            headers=self._authorize(),
        )

        self.assertEqual(failed.status_code, 500)
        self.assertTrue(material_file.is_file())
        self.assertEqual(list(self.paths.media_dir.glob("*.deleting")), [])
        with sqlite3.connect(self.paths.database_file) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM desktop_materials WHERE id = ?",
                    (material_id,),
                ).fetchone()[0],
                1,
            )
            connection.execute("DROP TRIGGER fail_material_delete")

        deleted = self.client.delete(
            f"/api/v1/materials/{material_id}",
            headers=self._authorize(),
        )
        repeated = self.client.delete(
            f"/api/v1/materials/{material_id}",
            headers=self._authorize(),
        )
        self.assertEqual(deleted.status_code, 200)
        self.assertFalse(material_file.exists())
        self.assertEqual(repeated.status_code, 404)

    def test_material_delete_cleans_record_when_file_is_already_missing(self):
        upload = self.client.post(
            "/api/v1/materials",
            data={"file": (io.BytesIO(self._mp4_bytes()), "missing.mp4")},
            content_type="multipart/form-data",
            headers=self._authorize(),
        )
        material_id = upload.get_json()["data"]["id"]
        with sqlite3.connect(self.paths.database_file) as connection:
            stored_name = connection.execute(
                "SELECT stored_name FROM desktop_materials WHERE id = ?",
                (material_id,),
            ).fetchone()[0]
        (self.paths.media_dir / stored_name).unlink()

        deleted = self.client.delete(
            f"/api/v1/materials/{material_id}",
            headers=self._authorize(),
        )
        repeated = self.client.delete(
            f"/api/v1/materials/{material_id}",
            headers=self._authorize(),
        )

        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(repeated.status_code, 404)

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
        app.config["SERVER_NAME"] = "127.0.0.1"
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
        self.assertIn("desktop_materials", names)

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
        module.app.config["SERVER_NAME"] = "127.0.0.1"
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
