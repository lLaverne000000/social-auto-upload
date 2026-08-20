"""Source-mode compatibility entry point for the private desktop API.

Production startup uses ``create_desktop_app`` from the desktop launcher with a
fresh in-memory token. This module exists for legacy imports and local
development only; it never exposes the application on a LAN interface.
"""

import secrets

from sau_desktop_api import create_desktop_app
from sau_desktop_service import JobManager
from sau_runtime import get_runtime_paths


_jobs = JobManager()
app = create_desktop_app(
    paths=get_runtime_paths(),
    session_token=secrets.token_urlsafe(32),
    jobs=_jobs,
)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5409)
