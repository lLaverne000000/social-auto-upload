"""Private loopback launcher for the Social Auto Upload desktop application."""

from __future__ import annotations

import importlib
import logging
import secrets
import sys
import threading
import webbrowser
from typing import Any, Literal

from werkzeug.serving import make_server

from sau_browser_runtime import configure_browser_environment
from sau_runtime import get_runtime_paths


_LOGGER = logging.getLogger(__name__)
_LOOPBACK_HOST = "127.0.0.1"
_WINDOW_TITLE = "Social Auto Upload"


def _exception_category(error: BaseException) -> str:
    name = type(error).__name__
    if not name.isidentifier():
        return "Exception"
    return name[:64]


class LoopbackServer:
    """Own a Werkzeug server, its daemon thread, and deterministic cleanup."""

    def __init__(self, server: Any) -> None:
        self._server = server
        self.url = f"http://{_LOOPBACK_HOST}:{int(server.server_port)}/"
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

    def wait_until_stopped(self) -> None:
        """Keep browser-fallback source mode alive until stop or Ctrl+C."""
        while not self._stopped.wait(timeout=0.25):
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
    server.start()
    return server


def open_desktop_window(url: str) -> Literal["webview", "browser"]:
    """Open PyWebView, falling back to the user's default browser."""
    try:
        webview = importlib.import_module("webview")
        webview.create_window(
            _WINDOW_TITLE,
            url,
            width=1200,
            height=800,
        )
        webview.start()
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
    jobs = None
    app = None
    server = None
    try:
        paths = get_runtime_paths()
        configure_browser_environment(
            paths,
            required=bool(getattr(sys, "frozen", False)),
        )

        # These imports reach sau_cli and uploader modules. They must remain after
        # browser configuration so every launch helper sees the verified payload.
        service_module = importlib.import_module("sau_desktop_service")
        api_module = importlib.import_module("sau_desktop_api")

        jobs = service_module.JobManager()
        app = api_module.create_desktop_app(
            paths=paths,
            session_token=secrets.token_urlsafe(32),
            jobs=jobs,
        )
        server = start_loopback_server(app)
        mode = open_desktop_window(server.url)
        if mode == "browser":
            server.wait_until_stopped()
    finally:
        _shutdown_components(server, app, jobs)


if __name__ == "__main__":
    main()


__all__ = ["LoopbackServer", "main", "open_desktop_window", "start_loopback_server"]
