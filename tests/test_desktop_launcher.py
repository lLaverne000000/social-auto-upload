from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import sau_desktop


class DesktopLauncherTests(unittest.TestCase):
    def _main_dependencies(self, *, window_result="webview", window_error=None):
        events: list[str] = []
        paths = Mock(name="runtime_paths")
        jobs = Mock(name="jobs")
        app_shutdown = Mock(name="app_shutdown", side_effect=lambda: events.append("app-shutdown"))
        jobs.shutdown.side_effect = lambda: events.append("jobs-shutdown")
        app = SimpleNamespace(extensions={"sau_desktop_shutdown": app_shutdown})
        server = Mock(name="server", url="http://127.0.0.1:49152/")
        server.shutdown.side_effect = lambda: events.append("server-shutdown")
        server.server_close.side_effect = lambda: events.append("server-close")

        service_module = SimpleNamespace(
            JobManager=Mock(
                name="JobManager",
                side_effect=lambda: (events.append("jobs-create"), jobs)[1],
            )
        )

        def create_app(**kwargs):
            events.append("app-create")
            self.assertIs(kwargs["paths"], paths)
            self.assertIs(kwargs["jobs"], jobs)
            self.assertEqual(kwargs["session_token"], "process-secret")
            return app

        api_module = SimpleNamespace(create_desktop_app=create_app)
        expected_server = server
        self._main_app = app

        def import_module(name):
            self.assertIn("browser-configure", events)
            events.append(f"import:{name}")
            if name == "sau_desktop_service":
                return service_module
            if name == "sau_desktop_api":
                return api_module
            self.fail(f"unexpected dynamic import: {name}")

        def configure(configured_paths, *, required):
            self.assertIs(configured_paths, paths)
            events.append("browser-configure")
            self.required_browser = required

        def start_server(created_app):
            self.assertIs(created_app, app)
            events.append("server-start")
            return server

        def open_window(url, *, server, stop_event, status_callback):
            self.assertEqual(url, "http://127.0.0.1:49152/")
            self.assertNotIn("process-secret", url)
            self.assertIs(server, expected_server)
            self.assertIsInstance(stop_event, threading.Event)
            self.assertTrue(callable(status_callback))
            events.append("window-open")
            if window_error is not None:
                raise window_error
            return window_result

        patches = (
            patch("sau_desktop.get_runtime_paths", side_effect=lambda: (events.append("paths"), paths)[1]),
            patch("sau_desktop.configure_browser_environment", side_effect=configure),
            patch("sau_desktop.secrets.token_urlsafe", return_value="process-secret"),
            patch("sau_desktop.start_loopback_server", side_effect=start_server),
            patch("sau_desktop.open_desktop_window", side_effect=open_window),
            patch("sau_desktop.importlib.import_module", side_effect=import_module),
        )
        return events, paths, jobs, app_shutdown, server, patches

    def test_module_import_does_not_import_cli_service_or_api(self):
        code = (
            "import sys; import sau_desktop; "
            "forbidden={'sau_cli','sau_desktop_service','sau_desktop_api'}; "
            "present=forbidden.intersection(sys.modules); "
            "assert not present, sorted(present)"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_main_configures_browser_before_dynamic_uploader_imports(self):
        events, _paths, _jobs, _app_shutdown, _server, patches = self._main_dependencies()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], \
             patch.object(sys, "frozen", True, create=True):
            sau_desktop.main()

        self.assertTrue(self.required_browser)
        self.assertEqual(events, [
            "paths",
            "browser-configure",
            "import:sau_desktop_service",
            "import:sau_desktop_api",
            "jobs-create",
            "app-create",
            "server-start",
            "window-open",
            "server-shutdown",
            "server-close",
            "app-shutdown",
            "jobs-shutdown",
        ])

    def test_main_reports_server_ready_to_exclusive_private_status_file(self):
        events, _paths, _jobs, _app_shutdown, _server, patches = self._main_dependencies()
        with tempfile.TemporaryDirectory() as temp:
            status_file = Path(temp) / "desktop.status"
            with patch.dict(
                os.environ,
                {"SAU_DESKTOP_STATUS_FILE": str(status_file)},
                clear=False,
            ), patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
                sau_desktop.main()

            lines = status_file.read_text(encoding="utf-8").splitlines()
            self.assertEqual(lines, [
                "starting",
                "runtime-ready",
                "browser-ready",
                "service-ready",
                "api-ready",
                "jobs-ready",
                "app-ready",
                "server-starting",
                "server-ready http://127.0.0.1:49152/",
                "window-returned webview",
                "stopped",
            ])
            self.assertNotIn("process-secret", status_file.read_text(encoding="utf-8"))
            if os.name != "nt":
                self.assertEqual(status_file.stat().st_mode & 0o777, 0o600)

    def test_status_file_refuses_to_overwrite_existing_path(self):
        _events, _paths, _jobs, _app_shutdown, _server, patches = self._main_dependencies()
        with tempfile.TemporaryDirectory() as temp:
            status_file = Path(temp) / "desktop.status"
            status_file.write_text("keep", encoding="utf-8")
            with patch.dict(
                os.environ,
                {"SAU_DESKTOP_STATUS_FILE": str(status_file)},
                clear=False,
            ), patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], \
                 self.assertRaises(FileExistsError):
                sau_desktop.main()
            self.assertEqual(status_file.read_text(encoding="utf-8"), "keep")

    def test_source_mode_allows_unbundled_browser(self):
        _events, _paths, _jobs, _app_shutdown, _server, patches = self._main_dependencies()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], \
             patch.object(sys, "frozen", False, create=True):
            sau_desktop.main()

        self.assertFalse(self.required_browser)

    def test_server_binds_literal_loopback_and_ephemeral_port(self):
        stop = threading.Event()
        raw_server = Mock(server_port=49152)
        raw_server.serve_forever.side_effect = lambda: stop.wait(timeout=2)
        raw_server.shutdown.side_effect = stop.set

        with patch("sau_desktop.make_server", return_value=raw_server) as make_server, \
             patch("sau_desktop._probe_server_health", return_value=True):
            server = sau_desktop.start_loopback_server(Mock())
            self.assertEqual(server.url, "http://127.0.0.1:49152/")
            self.assertTrue(server.thread.daemon)
            make_server.assert_called_once_with("127.0.0.1", 0, unittest.mock.ANY, threaded=True)
            server.shutdown()
            server.shutdown()
            server.server_close()
            server.server_close()
            server.thread.join(timeout=2)

        raw_server.shutdown.assert_called_once_with()
        raw_server.server_close.assert_called_once_with()

    def test_server_start_confirms_health_readiness(self):
        stop = threading.Event()
        raw_server = Mock(server_port=49152)
        raw_server.serve_forever.side_effect = lambda: stop.wait(timeout=2)
        raw_server.shutdown.side_effect = stop.set

        with patch("sau_desktop.make_server", return_value=raw_server), \
             patch("sau_desktop._probe_server_health", return_value=True) as probe:
            server = sau_desktop.start_loopback_server(Mock())
            server.shutdown()
            server.server_close()
            server.thread.join(timeout=2)

        probe.assert_called_with("http://127.0.0.1:49152/api/v1/health")

    def test_server_thread_failure_is_observable_without_sensitive_log_text(self):
        raw_server = Mock(server_port=49152)
        raw_server.serve_forever.side_effect = RuntimeError(
            "secret-token /Users/operator/private/browser"
        )
        logger = Mock()
        with patch("sau_desktop._LOGGER", logger):
            server = sau_desktop.LoopbackServer(raw_server)
            server.start()
            server.thread.join(timeout=2)
            with self.assertRaises(RuntimeError) as raised:
                server.raise_if_failed()

        rendered_log = " ".join(str(value) for item in logger.method_calls for value in item.args)
        self.assertIn("RuntimeError", str(raised.exception))
        self.assertNotIn("secret-token", str(raised.exception))
        self.assertNotIn("/Users/operator", str(raised.exception))
        self.assertNotIn("secret-token", rendered_log)
        self.assertNotIn("/Users/operator", rendered_log)
        self.assertIn("RuntimeError", rendered_log)

    def test_exception_location_reports_code_identity_without_message_or_path(self):
        try:
            raise TypeError("secret-token C:\\Users\\operator\\private")
        except TypeError as error:
            location = sau_desktop._exception_location(error)

        self.assertIn("test_desktop_launcher", location)
        self.assertIn(
            "test_exception_location_reports_code_identity_without_message_or_path",
            location,
        )
        self.assertNotIn("secret-token", location)
        self.assertNotIn("C:\\Users", location)

    def test_thread_start_failure_closes_bound_server(self):
        raw_server = Mock(server_port=49152)
        thread = Mock()
        thread.start.side_effect = RuntimeError("thread unavailable")
        with patch("sau_desktop.make_server", return_value=raw_server), \
             patch("sau_desktop.threading.Thread", return_value=thread):
            with self.assertRaisesRegex(RuntimeError, "thread unavailable"):
                sau_desktop.start_loopback_server(Mock())
        raw_server.server_close.assert_called_once_with()

    def test_webview_uses_requested_size(self):
        webview = Mock()
        server = Mock()
        server.wait_until_stopped.side_effect = (
            lambda _stop, cancel: cancel.wait(timeout=1)
        )
        with patch("sau_desktop.importlib.import_module", return_value=webview), \
             patch("sau_desktop.webbrowser.open") as open_browser:
            mode = sau_desktop.open_desktop_window(
                "http://127.0.0.1:49152/",
                server=server,
                stop_event=threading.Event(),
            )

        self.assertEqual(mode, "webview")
        webview.create_window.assert_called_once_with(
            "Social Auto Upload",
            "http://127.0.0.1:49152/",
            width=1200,
            height=800,
        )
        webview.start.assert_called_once_with(func=unittest.mock.ANY)
        open_browser.assert_not_called()

    def test_webview_monitor_starts_after_backend_initialization(self):
        stop_event = threading.Event()
        stop_event.set()
        shown = threading.Event()
        closed = threading.Event()
        backend_calls: list[str] = []

        class Backend:
            def destroy_window(self, uid):
                backend_calls.append(uid)
                closed.set()

        window = SimpleNamespace(
            uid="master",
            gui=None,
            events=SimpleNamespace(shown=shown, closed=closed),
            destroy=Mock(side_effect=AssertionError("public destroy waits for shown")),
        )
        server = Mock()
        server.wait_until_stopped.side_effect = lambda stop, _cancel: stop.wait(timeout=1)
        webview = Mock()
        webview.create_window.return_value = window

        def start_webview(*, func):
            window.gui = Backend()
            func()

        webview.start.side_effect = start_webview
        status_events: list[str] = []
        with patch("sau_desktop.importlib.import_module", return_value=webview):
            mode = sau_desktop.open_desktop_window(
                "http://127.0.0.1:49152/",
                server=server,
                stop_event=stop_event,
                status_callback=status_events.append,
            )

        self.assertEqual(mode, "webview")
        self.assertEqual(backend_calls, ["master"])
        window.destroy.assert_not_called()
        self.assertEqual(status_events, [
            "webview-imported",
            "webview-window-created",
            "webview-loop-starting",
            "webview-monitor-started",
            "webview-close-requested",
            "webview-close-complete",
            "webview-loop-returned",
        ])

    def test_webview_close_bypasses_shown_wait_during_early_protected_quit(self):
        shown = threading.Event()
        closed = threading.Event()
        backend_calls: list[str] = []

        class Backend:
            def destroy_window(self, uid):
                backend_calls.append(uid)
                if len(backend_calls) >= 2:
                    closed.set()

        window = SimpleNamespace(
            uid="master",
            gui=Backend(),
            events=SimpleNamespace(shown=shown, closed=closed),
            destroy=Mock(side_effect=AssertionError("public destroy waits for shown")),
        )

        sau_desktop._destroy_webview_window(window, timeout=0.5)

        self.assertGreaterEqual(len(backend_calls), 2)
        self.assertEqual(set(backend_calls), {"master"})
        window.destroy.assert_not_called()

    def test_live_server_failure_destroys_webview_and_is_observable(self):
        server_ready = threading.Event()
        crash_server = threading.Event()
        window_destroyed = threading.Event()
        raw_server = Mock(server_port=49152)

        def serve_forever():
            server_ready.set()
            if not crash_server.wait(timeout=2):
                return
            raise RuntimeError("server crashed after readiness")

        raw_server.serve_forever.side_effect = serve_forever
        server = sau_desktop.LoopbackServer(raw_server)
        server.start()
        self.assertTrue(server_ready.wait(timeout=2))

        window = Mock()
        window.destroy.side_effect = window_destroyed.set
        webview = Mock()
        webview.create_window.return_value = window

        def webview_start(*, func):
            crash_server.set()
            func()
            self.assertTrue(window_destroyed.wait(timeout=2))

        webview.start.side_effect = webview_start
        try:
            with patch("sau_desktop.importlib.import_module", return_value=webview):
                sau_desktop.open_desktop_window(
                    server.url,
                    server=server,
                    stop_event=threading.Event(),
                )
            server.thread.join(timeout=2)
            window.destroy.assert_called_once_with()
            with self.assertRaisesRegex(RuntimeError, "RuntimeError"):
                server.raise_if_failed()
        finally:
            crash_server.set()
            server.thread.join(timeout=2)
            server.server_close()

    def test_main_propagates_webview_server_failure_after_full_cleanup(self):
        server_ready = threading.Event()
        crash_server = threading.Event()
        window_destroyed = threading.Event()
        raw_server = Mock(server_port=49152)

        def serve_forever():
            server_ready.set()
            crash_server.wait(timeout=2)
            raise RuntimeError("post-readiness failure")

        raw_server.serve_forever.side_effect = serve_forever
        server = sau_desktop.LoopbackServer(raw_server)
        server.start()
        self.assertTrue(server_ready.wait(timeout=2))

        events = []
        jobs = Mock()
        jobs.shutdown.side_effect = lambda: events.append("jobs-shutdown")
        app_shutdown = Mock(side_effect=lambda: events.append("app-shutdown"))
        app = SimpleNamespace(extensions={"sau_desktop_shutdown": app_shutdown})
        service = SimpleNamespace(JobManager=Mock(return_value=jobs))
        api = SimpleNamespace(create_desktop_app=Mock(return_value=app))
        window = Mock()
        window.destroy.side_effect = window_destroyed.set
        webview = Mock()
        webview.create_window.return_value = window

        def start_webview(*, func):
            crash_server.set()
            func()
            self.assertTrue(window_destroyed.wait(timeout=2))

        webview.start.side_effect = start_webview
        modules = {
            "sau_desktop_service": service,
            "sau_desktop_api": api,
            "webview": webview,
        }
        try:
            with patch("sau_desktop.get_runtime_paths", return_value=Mock()), \
                 patch("sau_desktop.configure_browser_environment"), \
                 patch("sau_desktop.secrets.token_urlsafe", return_value="secret"), \
                 patch("sau_desktop.start_loopback_server", return_value=server), \
                 patch("sau_desktop.webbrowser.open"), \
                 patch("sau_desktop.importlib.import_module", side_effect=modules.__getitem__):
                with self.assertRaisesRegex(RuntimeError, "RuntimeError"):
                    sau_desktop.main()
        finally:
            crash_server.set()
            server.thread.join(timeout=2)
            server.server_close()

        window.destroy.assert_called_once_with()
        self.assertEqual(events, ["app-shutdown", "jobs-shutdown"])

    def test_webview_failure_falls_back_without_logging_sensitive_reason(self):
        logger = Mock()
        with patch("sau_desktop.webbrowser.open") as open_browser, \
             patch("sau_desktop._LOGGER", logger), \
             patch(
                 "sau_desktop.importlib.import_module",
                 side_effect=RuntimeError("token /Users/operator/private"),
             ):
            mode = sau_desktop.open_desktop_window(
                "http://127.0.0.1:49152/",
                server=Mock(),
                stop_event=threading.Event(),
            )

        self.assertEqual(mode, "browser")
        open_browser.assert_called_once_with("http://127.0.0.1:49152/")
        rendered_log = " ".join(str(value) for item in logger.method_calls for value in item.args)
        self.assertNotIn("token", rendered_log)
        self.assertNotIn("/Users/operator", rendered_log)
        self.assertIn("RuntimeError", rendered_log)

    def test_browser_fallback_waits_before_shutdown(self):
        events, _paths, _jobs, _app_shutdown, server, patches = self._main_dependencies(
            window_result="browser"
        )
        def stop_from_gui(stop_event):
            self._main_app.extensions["sau_desktop_stop"]()
            self.assertTrue(stop_event.is_set())
            events.append("browser-wait")

        server.wait_until_stopped.side_effect = stop_from_gui
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            sau_desktop.main()

        self.assertLess(events.index("browser-wait"), events.index("server-shutdown"))
        server.wait_until_stopped.assert_called_once_with(unittest.mock.ANY)

    def test_shutdown_order_runs_all_hooks_on_keyboard_interrupt(self):
        events, _paths, jobs, app_shutdown, server, patches = self._main_dependencies(
            window_error=KeyboardInterrupt()
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            with self.assertRaises(KeyboardInterrupt):
                sau_desktop.main()

        self.assertEqual(events[-4:], [
            "server-shutdown",
            "server-close",
            "app-shutdown",
            "jobs-shutdown",
        ])
        server.shutdown.assert_called_once_with()
        server.server_close.assert_called_once_with()
        app_shutdown.assert_called_once_with()
        jobs.shutdown.assert_called_once_with()

    def test_server_start_failure_still_shuts_down_app_and_jobs(self):
        events, _paths, jobs, app_shutdown, _server, patches = self._main_dependencies()
        failing_start = patch(
            "sau_desktop.start_loopback_server",
            side_effect=RuntimeError("server start failed"),
        )
        with patches[0], patches[1], patches[2], failing_start, patches[4], patches[5]:
            with self.assertRaisesRegex(RuntimeError, "server start failed"):
                sau_desktop.main()

        app_shutdown.assert_called_once_with()
        jobs.shutdown.assert_called_once_with()
        self.assertEqual(events[-2:], ["app-shutdown", "jobs-shutdown"])

    def test_app_creation_failure_still_shuts_down_jobs(self):
        paths = Mock()
        jobs = Mock()
        service = SimpleNamespace(JobManager=Mock(return_value=jobs))
        api = SimpleNamespace(create_desktop_app=Mock(side_effect=RuntimeError("app failed")))
        imports = {"sau_desktop_service": service, "sau_desktop_api": api}
        with patch("sau_desktop.get_runtime_paths", return_value=paths), \
             patch("sau_desktop.configure_browser_environment"), \
             patch("sau_desktop.secrets.token_urlsafe", return_value="secret"), \
             patch("sau_desktop.importlib.import_module", side_effect=imports.__getitem__):
            with self.assertRaisesRegex(RuntimeError, "app failed"):
                sau_desktop.main()

        jobs.shutdown.assert_called_once_with()

    def test_pyproject_exposes_gui_entrypoint_and_all_top_level_runtime_modules(self):
        source = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(
            encoding="utf-8"
        )
        self.assertRegex(
            source,
            r"(?ms)^\[project\.gui-scripts\]\s+sau-desktop\s*=\s*\"sau_desktop:main\"",
        )
        match = re.search(
            r"^py-modules\s*=\s*\[([^]]*)\]",
            source,
            flags=re.MULTILINE,
        )
        self.assertIsNotNone(match)
        modules = set(re.findall(r'\"([^\"]+)\"', match.group(1)))
        self.assertTrue({
            "conf",
            "sau_backend",
            "sau_browser_runtime",
            "sau_cli",
            "sau_desktop",
            "sau_desktop_api",
            "sau_desktop_service",
            "sau_media_validation",
            "sau_runtime",
        }.issubset(modules))


if __name__ == "__main__":
    unittest.main()
