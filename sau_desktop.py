"""Private loopback launcher for the Social Auto Upload desktop application."""

from __future__ import annotations

import importlib
import json
import logging
import os
import secrets
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from typing import Any, Literal

from werkzeug.serving import make_server

from sau_browser_runtime import configure_browser_environment
from sau_runtime import get_runtime_paths


_LOGGER = logging.getLogger(__name__)
_LOOPBACK_HOST = "127.0.0.1"
_WINDOW_TITLE = "Social Auto Upload"
_SERVER_READY_TIMEOUT_SECONDS = 5.0
_STATUS_FILE_ENV = "SAU_DESKTOP_STATUS_FILE"


def _exception_category(error: BaseException) -> str:
    name = type(error).__name__
    if not name.isidentifier():
        return "Exception"
    return name[:64]


def _exception_location(error: BaseException) -> str:
    """Return only traceback code identity, never exception text or local values."""
    traceback = error.__traceback__
    if traceback is None:
        return "unknown:unknown:0"
    while traceback.tb_next is not None:
        traceback = traceback.tb_next
    frame = traceback.tb_frame

    def safe_identifier(value: object) -> str:
        cleaned = "".join(
            character
            for character in str(value)
            if character.isalnum() or character in "._<>"
        )
        return cleaned[:128] or "unknown"

    module = safe_identifier(frame.f_globals.get("__name__", "unknown"))
    function = safe_identifier(frame.f_code.co_name)
    return f"{module}:{function}:{int(traceback.tb_lineno)}"


class _DesktopStatusReporter:
    """Optional no-secret startup handshake for native smoke verification."""

    def __init__(self) -> None:
        raw_path = os.environ.get(_STATUS_FILE_ENV)
        self._stream: Any | None = None
        if raw_path is None:
            return
        if not raw_path or "\x00" in raw_path:
            raise ValueError(f"{_STATUS_FILE_ENV} must name a new file")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(raw_path, flags, 0o600)
        self._stream = os.fdopen(
            descriptor,
            "w",
            encoding="utf-8",
            newline="\n",
            buffering=1,
        )

    def emit(self, event: str, value: str | None = None) -> None:
        if self._stream is None:
            return
        line = event if value is None else f"{event} {value}"
        if "\r" in line or "\n" in line:
            raise ValueError("desktop status lines must be single-line")
        self._stream.write(f"{line}\n")
        self._stream.flush()
        os.fsync(self._stream.fileno())

    def close(self) -> None:
        if self._stream is not None:
            self._stream.close()
            self._stream = None


def _probe_server_health(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=0.25) as response:
            if response.status != 200:
                return False
            body = response.read(4096)
    except (OSError, urllib.error.URLError):
        return False
    try:
        envelope = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    return (
        isinstance(envelope, dict)
        and envelope.get("ok") is True
        and isinstance(envelope.get("data"), dict)
        and envelope["data"].get("status") == "ok"
    )


class LoopbackServer:
    """Own a Werkzeug server, its daemon thread, and deterministic cleanup."""

    def __init__(self, server: Any) -> None:
        self._server = server
        self.url = f"http://{_LOOPBACK_HOST}:{int(server.server_port)}/"
        self.health_url = f"{self.url}api/v1/health"
        self._failure: BaseException | None = None
        self._stopped = threading.Event()
        self._cleanup_lock = threading.Lock()
        self._shutdown_called = False
        self._close_called = False
        self.thread = threading.Thread(
            target=self._serve,
            name="sau-loopback-server",
            daemon=True,
        )

    def start(self) -> None:
        try:
            self.thread.start()
        except BaseException:
            self.server_close()
            raise

    def _serve(self) -> None:
        try:
            self._server.serve_forever()
        except BaseException as error:
            self._failure = error
            _LOGGER.error(
                "Local desktop server stopped unexpectedly (%s).",
                _exception_category(error),
            )
        finally:
            self._stopped.set()

    def raise_if_failed(self) -> None:
        if self._failure is not None:
            category = _exception_category(self._failure)
            raise RuntimeError(
                f"Local desktop server stopped unexpectedly ({category})."
            ) from None

    def wait_until_ready(self, timeout: float = _SERVER_READY_TIMEOUT_SECONDS) -> None:
        deadline = time.monotonic() + timeout
        while True:
            if self._stopped.is_set():
                self.raise_if_failed()
                raise RuntimeError("Local desktop server stopped before it was ready.")
            if _probe_server_health(self.health_url):
                return
            if time.monotonic() >= deadline:
                raise RuntimeError("Local desktop server health check timed out.")
            self._stopped.wait(timeout=0.05)

    def wait_until_stopped(
        self,
        stop_event: threading.Event | None = None,
        cancel_event: threading.Event | None = None,
    ) -> None:
        """Wait for server failure, application stop, cancellation, or Ctrl+C."""
        while True:
            if stop_event is not None and stop_event.is_set():
                return
            if cancel_event is not None and cancel_event.is_set():
                return
            if self._stopped.wait(timeout=0.05):
                break
            self.raise_if_failed()
        self.raise_if_failed()

    def shutdown(self) -> None:
        with self._cleanup_lock:
            if self._shutdown_called:
                return
            self._shutdown_called = True
        if self.thread.is_alive():
            self._server.shutdown()

    def server_close(self) -> None:
        with self._cleanup_lock:
            if self._close_called:
                return
            self._close_called = True
        self._server.server_close()


def start_loopback_server(app: Any) -> LoopbackServer:
    """Bind the desktop API to a literal loopback address and OS-selected port."""
    raw_server = make_server(_LOOPBACK_HOST, 0, app, threaded=True)
    server = LoopbackServer(raw_server)
    try:
        server.start()
        server.wait_until_ready()
    except BaseException:
        server.shutdown()
        server.server_close()
        raise
    return server


def _destroy_webview_window(window: Any, *, timeout: float = 5.0) -> None:
    """Close even when an early quit arrives before PyWebView's shown event.

    PyWebView 5.4 decorates ``Window.destroy`` with a 20-second wait for the
    shown event. Windows service-backed CI can initialize WinForms without ever
    emitting that event, so use the already initialized backend directly until
    the closed event confirms completion.
    """
    events = getattr(window, "events", None)
    shown = getattr(events, "shown", None)
    closed = getattr(events, "closed", None)
    if not all(
        callable(getattr(event, method, None))
        for event, method in ((shown, "is_set"), (closed, "is_set"), (closed, "wait"))
    ):
        window.destroy()
        return
    shown_state = shown.is_set()
    closed_state = closed.is_set()
    if type(shown_state) is not bool or type(closed_state) is not bool:
        window.destroy()
        return

    deadline = time.monotonic() + timeout
    while True:
        if closed_state:
            return
        if shown_state:
            window.destroy()
            return

        gui = getattr(window, "gui", None)
        destroy_window = getattr(gui, "destroy_window", None)
        uid = getattr(window, "uid", None)
        if callable(destroy_window) and isinstance(uid, str) and uid:
            try:
                destroy_window(uid)
            except Exception:
                pass

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("Desktop window did not close after protected quit.")
        if closed.wait(timeout=min(0.05, remaining)):
            return
        shown_state = shown.is_set()
        closed_state = closed.is_set()


def open_desktop_window(
    url: str,
    *,
    server: LoopbackServer,
    stop_event: threading.Event,
) -> Literal["webview", "browser"]:
    """Open PyWebView, falling back to the user's default browser."""
    try:
        webview = importlib.import_module("webview")
        window = webview.create_window(
            _WINDOW_TITLE,
            url,
            width=1200,
            height=800,
        )
        monitor_cancel = threading.Event()

        def monitor_server() -> None:
            try:
                server.wait_until_stopped(stop_event, monitor_cancel)
            except RuntimeError:
                pass
            if monitor_cancel.is_set():
                return
            try:
                _destroy_webview_window(window)
            except Exception as error:
                _LOGGER.error(
                    "Desktop window close failed (%s).",
                    _exception_category(error),
                )

        monitor = threading.Thread(
            target=monitor_server,
            name="sau-webview-monitor",
            daemon=True,
        )
        monitor.start()
        try:
            webview.start()
        finally:
            monitor_cancel.set()
            monitor.join(timeout=1.0)
        return "webview"
    except Exception as error:
        _LOGGER.error(
            "Desktop window unavailable (%s); using the default browser.",
            _exception_category(error),
        )
        webbrowser.open(url)
        return "browser"


def _cleanup_call(callback: Any, label: str) -> None:
    if not callable(callback):
        return
    try:
        callback()
    except Exception as error:
        _LOGGER.error(
            "%s cleanup failed (%s).",
            label,
            _exception_category(error),
        )


def _shutdown_components(server: Any, app: Any, jobs: Any) -> None:
    if server is not None:
        _cleanup_call(getattr(server, "shutdown", None), "Server shutdown")
        _cleanup_call(getattr(server, "server_close", None), "Server close")
    if app is not None:
        extensions = getattr(app, "extensions", {})
        hook = extensions.get("sau_desktop_shutdown") if isinstance(extensions, dict) else None
        _cleanup_call(hook, "Desktop API")
    if jobs is not None:
        _cleanup_call(getattr(jobs, "shutdown", None), "Publish jobs")


def main() -> None:
    """Configure the offline runtime, start the private API, and own its lifecycle."""
    status = _DesktopStatusReporter()
    jobs = None
    app = None
    server = None
    stop_event = threading.Event()
    try:
        status.emit("starting")
        try:
            paths = get_runtime_paths()
            status.emit("runtime-ready")
            configure_browser_environment(
                paths,
                required=bool(getattr(sys, "frozen", False)),
            )
            status.emit("browser-ready")

            # These imports reach sau_cli and uploader modules. They must remain after
            # browser configuration so every launch helper sees the verified payload.
            service_module = importlib.import_module("sau_desktop_service")
            status.emit("service-ready")
            api_module = importlib.import_module("sau_desktop_api")
            status.emit("api-ready")

            jobs = service_module.JobManager()
            status.emit("jobs-ready")
            app = api_module.create_desktop_app(
                paths=paths,
                session_token=secrets.token_urlsafe(32),
                jobs=jobs,
            )
            status.emit("app-ready")
            app.extensions["sau_desktop_stop"] = stop_event.set
            status.emit("server-starting")
            server = start_loopback_server(app)
            status.emit("server-ready", server.url)
            mode = open_desktop_window(
                server.url,
                server=server,
                stop_event=stop_event,
            )
            status.emit("window-returned", mode)
            if mode == "browser":
                server.wait_until_stopped(stop_event)
        finally:
            _shutdown_components(server, app, jobs)
            if server is not None:
                server.raise_if_failed()
    except BaseException as error:
        status.emit(
            "error",
            f"{_exception_category(error)} {_exception_location(error)}",
        )
        raise
    finally:
        status.emit("stopped")
        status.close()


if __name__ == "__main__":
    main()


__all__ = ["LoopbackServer", "main", "open_desktop_window", "start_loopback_server"]
