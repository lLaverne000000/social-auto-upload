# Cross-Platform Offline Desktop Packaging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Package Social Auto Upload as one offline macOS Intel/Apple Silicon installer and one offline Windows x64 installer, with the existing Vue GUI as the normal entry point and the complete CLI preserved.

**Architecture:** Separate immutable packaged resources from per-user writable data, configure the bundled Patchright Chromium before uploader imports, and run the compiled Vue GUI through a token-protected loopback Flask server. GUI publish jobs are translated into the same `sau_cli` parser/dispatcher path used by the CLI so mandatory governance cannot be bypassed.

**Tech Stack:** Python 3.12, Flask/Werkzeug, Vue 3/Vite, Patchright 1.58.2, PyWebView with system-browser fallback, PyInstaller, macOS `productbuild`, Inno Setup, GitHub Actions native runners.

**Spec:** `docs/superpowers/specs/2026-08-20-cross-platform-offline-desktop-packaging-design.md`

## Global Constraints

- Deliver exactly two user-facing installers: one macOS `.pkg` containing native `x86_64` and `arm64` payloads, and one Windows 10/11 x64 `.exe`.
- Installation and first launch must not require Codex, Python, Node.js, or a Chromium download.
- GUI coverage stays limited to the four platforms already present in the Vue GUI: Douyin, Kuaishou, WeChat Channels, and Xiaohongshu. The CLI retains all existing platforms.
- Runtime data roots are `~/Library/Application Support/SocialAutoUpload` on macOS and `%LOCALAPPDATA%\SocialAutoUpload` on Windows.
- Installers must not contain cookies, browser profiles, uploaded media, audit/evidence records, user paths, or development secrets.
- The first release is unsigned; signing/notarization is not attempted without operator-provided credentials.
- Phase 3C remains unchanged: no daily quota, no forced headed mode, no removal of automatic publishing, and no publishing time windows.
- No CAPTCHA solving, fingerprint spoofing, proxy rotation, challenge bypass, or platform-control evasion.
- Never claim Apple Silicon or Windows verification from the Intel Mac; smoke tests must run on matching native targets.
- Checkpoint commits listed below require explicit operator authorization; do not commit or push merely because the plan contains a commit command.

---

## File Map

**Create**

- `sau_runtime.py`: packaged resource discovery and platform-standard writable paths.
- `sau_browser_runtime.py`: bundled Chromium manifest parsing, integrity checking, and Patchright environment setup.
- `sau_desktop_service.py`: GUI request validation, CLI argument translation, and background job state.
- `sau_desktop_api.py`: loopback-only Flask app factory, static Vue serving, session enforcement, and desktop API.
- `sau_desktop.py`: GUI process entry point, local server lifecycle, WebView launch, and browser fallback.
- `tests/test_runtime_paths.py`: runtime/resource path regression tests.
- `tests/test_browser_runtime.py`: browser manifest and offline failure tests.
- `tests/test_desktop_service.py`: GUI-to-CLI translation and job-state tests.
- `tests/test_desktop_api.py`: token, same-origin, loopback, and API contract tests.
- `tests/test_desktop_launcher.py`: port/server/WebView fallback lifecycle tests.
- `tests/test_release_packaging.py`: payload leak, architecture, manifest, and artifact-name tests.
- `packaging/pyinstaller/social_auto_upload.spec`: two-entry-point frozen distribution.
- `release_tools/__init__.py`: importable release-tool package marker.
- `release_tools/stage_browser.py`: clean browser staging and manifest creation.
- `release_tools/verify_release.py`: secret/user-data/path/architecture release gate.
- `packaging/macos/launcher`: architecture-selecting app/CLI launcher.
- `packaging/macos/build_pkg.sh`: combined `.app` and `.pkg` assembly.
- `packaging/windows/SocialAutoUpload.iss`: current-user offline installer definition.
- `packaging/windows/build_installer.ps1`: Windows payload and installer build.
- `.github/workflows/desktop-release.yml`: native target build, smoke-test, artifact, and Mac assembly jobs.
- `docs/desktop-install.md`: install, launch, warning, uninstall, and data-location guide.

**Modify**

- `conf.py`, `conf.example.py`: derive defaults from `sau_runtime` without storing writable data beside code.
- `sau_cli.py`: configure browser resources before uploader imports and delegate runtime-home resolution.
- `sau_backend.py`: keep legacy routes compatible while removing production `0.0.0.0` startup.
- `pyproject.toml`: add desktop/build extras and GUI entry point.
- `sau_frontend/src/utils/request.js`: use same-origin API and desktop session cookie.
- `sau_frontend/src/views/PublishCenter.vue`: call `/api/v1/publish`, render structured job state, and preserve safe defaults.
- `sau_frontend/src/views/AccountManagement.vue`: use same-origin SSE/API URLs.
- `sau_frontend/src/App.vue`: display the cross-computer same-account concurrency warning.
- `README.md`, `docs/install.md`, `docs/CLI.md`: document packaged and source modes.

### Task 1: Runtime and Resource Path Boundary

**Files:**
- Create: `sau_runtime.py`
- Create: `tests/test_runtime_paths.py`
- Modify: `conf.py`
- Modify: `conf.example.py`
- Modify: `sau_cli.py:276-287`

**Interfaces:**
- Produces: `RuntimePaths`, `resolve_resource_root()`, `resolve_data_root()`, `get_runtime_paths(create: bool = True)`.
- Consumers: browser staging, desktop API, desktop launcher, CLI account paths, release verification.

- [ ] **Step 1: Write failing runtime-path tests**

```python
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sau_runtime


class RuntimePathTests(unittest.TestCase):
    def test_macos_data_root_is_application_support(self):
        with patch.object(sau_runtime.platform, "system", return_value="Darwin"), \
             patch.dict(os.environ, {"HOME": "/Users/tester"}, clear=True):
            self.assertEqual(
                sau_runtime.resolve_data_root(),
                Path("/Users/tester/Library/Application Support/SocialAutoUpload"),
            )

    def test_windows_data_root_uses_localappdata(self):
        with patch.object(sau_runtime.platform, "system", return_value="Windows"), \
             patch.dict(os.environ, {"LOCALAPPDATA": r"C:\Users\tester\AppData\Local"}, clear=True):
            self.assertEqual(
                sau_runtime.resolve_data_root(),
                Path(r"C:\Users\tester\AppData\Local") / "SocialAutoUpload",
            )

    def test_explicit_home_override_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"SOCIAL_AUTO_UPLOAD_HOME": tmp}, clear=False):
                self.assertEqual(sau_runtime.resolve_data_root(), Path(tmp).resolve())

    def test_get_runtime_paths_does_not_create_when_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            with patch.dict(os.environ, {"SOCIAL_AUTO_UPLOAD_HOME": str(root)}, clear=False):
                paths = sau_runtime.get_runtime_paths(create=False)
            self.assertEqual(paths.data_root, root)
            self.assertFalse(root.exists())
```

- [ ] **Step 2: Run the tests and verify the module is missing**

Run: `.venv/bin/python -m unittest tests.test_runtime_paths -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'sau_runtime'`.

- [ ] **Step 3: Implement the focused runtime-path module**

```python
@dataclass(frozen=True, slots=True)
class RuntimePaths:
    resource_root: Path
    data_root: Path
    cookies_dir: Path
    profiles_dir: Path
    logs_dir: Path
    safety_dir: Path
    media_dir: Path
    database_file: Path

def resolve_resource_root() -> Path:
    frozen_root = getattr(sys, "_MEIPASS", None)
    return Path(frozen_root).resolve() if frozen_root else Path(__file__).parent.resolve()

def resolve_data_root() -> Path:
    override = os.environ.get("SOCIAL_AUTO_UPLOAD_HOME")
    if override:
        return Path(override).expanduser().resolve()
    system = platform.system()
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "SocialAutoUpload"
    if system == "Windows":
        local = os.environ.get("LOCALAPPDATA")
        if not local:
            raise RuntimeError("LOCALAPPDATA is required on Windows")
        return Path(local) / "SocialAutoUpload"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "SocialAutoUpload"

def get_runtime_paths(*, create: bool = True) -> RuntimePaths:
    data_root = resolve_data_root()
    paths = RuntimePaths(
        resource_root=resolve_resource_root(),
        data_root=data_root,
        cookies_dir=data_root / "cookies",
        profiles_dir=data_root / "profiles",
        logs_dir=data_root / "logs",
        safety_dir=data_root / ".sau_safety",
        media_dir=data_root / "media",
        database_file=data_root / "db" / "database.db",
    )
    if create:
        for directory in (
            paths.data_root, paths.cookies_dir, paths.profiles_dir,
            paths.logs_dir, paths.safety_dir, paths.media_dir,
            paths.database_file.parent,
        ):
            directory.mkdir(parents=True, exist_ok=True)
    return paths
```

Update `sau_cli.resolve_runtime_home()` to return `get_runtime_paths().data_root`; make `conf.BASE_DIR` the data root and add `RESOURCE_DIR` for immutable assets.

- [ ] **Step 4: Run focused and existing CLI tests**

Run: `.venv/bin/python -m unittest tests.test_runtime_paths tests.test_sau_browser_cli tests.test_safety_observability -v`

Expected: PASS.

- [ ] **Step 5: Checkpoint commit, only if explicitly authorized**

```bash
git add sau_runtime.py conf.py conf.example.py sau_cli.py tests/test_runtime_paths.py
git commit -m "feat: separate packaged resources from runtime data"
```

### Task 2: Offline Chromium Resolver

**Files:**
- Create: `sau_browser_runtime.py`
- Create: `tests/test_browser_runtime.py`
- Modify: `sau_cli.py:1-77`
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: `RuntimePaths.resource_root` from Task 1.
- Produces: `BrowserPayload`, `resolve_bundled_browser(paths, required)`, `configure_browser_environment(paths, required)`.
- Consumers: CLI startup, desktop startup, PyInstaller staging, release smoke tests.

- [ ] **Step 1: Write failing browser-manifest tests**

```python
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sau_runtime import RuntimePaths
import sau_browser_runtime


class BrowserRuntimeTests(unittest.TestCase):
    def make_paths(self, root: Path) -> RuntimePaths:
        return RuntimePaths(
            root, root / "data", root / "data/cookies", root / "data/profiles",
            root / "data/logs", root / "data/.sau_safety", root / "data/media",
            root / "data/db/database.db",
        )

    def test_resolves_matching_payload_and_sets_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            executable = root / "browsers" / "darwin-x86_64" / "chrome"
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"browser")
            digest = hashlib.sha256(b"browser").hexdigest()
            (root / "browser-manifest.json").write_text(json.dumps({
                "revision": "1208",
                "payloads": {
                    "darwin-x86_64": {
                        "executable": "browsers/darwin-x86_64/chrome",
                        "sha256": digest,
                    }
                },
            }), encoding="utf-8")
            with patch.object(sau_browser_runtime.platform, "system", return_value="Darwin"), \
                 patch.object(sau_browser_runtime.platform, "machine", return_value="x86_64"):
                payload = sau_browser_runtime.configure_browser_environment(
                    self.make_paths(root), required=True
                )
            self.assertEqual(payload.executable, executable)
            self.assertEqual(os.environ["SAU_CHROMIUM_EXECUTABLE"], str(executable))

    def test_frozen_mode_fails_closed_when_manifest_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError, "browser-manifest.json"):
                sau_browser_runtime.resolve_bundled_browser(
                    self.make_paths(Path(tmp)), required=True
                )
```

- [ ] **Step 2: Run the tests and verify failure**

Run: `.venv/bin/python -m unittest tests.test_browser_runtime -v`

Expected: FAIL because `sau_browser_runtime` does not exist.

- [ ] **Step 3: Implement manifest parsing and integrity checks**

Implement key normalization (`Darwin/x86_64 -> darwin-x86_64`, `Darwin/arm64 -> darwin-arm64`, `Windows/AMD64 -> windows-x86_64`), reject absolute or escaping executable paths, compare SHA-256, and set:

```python
os.environ["SAU_CHROMIUM_EXECUTABLE"] = str(payload.executable)
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(payload.executable.parent.parent)
```

In `sau_cli.py`, call `configure_browser_environment(get_runtime_paths(), required=bool(getattr(sys, "frozen", False)))` before importing any uploader module. Uploader launch helpers should prefer `SAU_CHROMIUM_EXECUTABLE` as `executable_path` without altering browser identity or adding stealth flags.

Add desktop dependencies under an extra, not the core dependency set:

```toml
desktop = [
  "Flask[async]==3.1.1",
  "pywebview>=5.4,<6",
]
build = [
  "pyinstaller>=6.14,<7",
]
```

- [ ] **Step 4: Run browser, uploader, and governance tests**

Run: `.venv/bin/python -m unittest tests.test_browser_runtime tests.test_sau_browser_cli tests.test_douyin_declaration tests.test_xiaohongshu_uploader -v`

Expected: PASS without downloading a browser.

- [ ] **Step 5: Checkpoint commit, only if explicitly authorized**

```bash
git add sau_browser_runtime.py sau_cli.py pyproject.toml tests/test_browser_runtime.py
git commit -m "feat: resolve bundled Chromium offline"
```

### Task 3: Shared GUI-to-CLI Command Service

**Files:**
- Create: `sau_desktop_service.py`
- Create: `tests/test_desktop_service.py`
- Modify: `sau_backend.py`

**Interfaces:**
- Consumes: `sau_cli.build_parser()` and `sau_cli.dispatch()`.
- Produces: `PublishRequest`, `JobStatus`, `PublishJob`, `build_publish_argv()`, `JobManager.submit()`, `JobManager.get()`, `JobManager.wait()`.
- Consumers: desktop API and Vue publish center.

- [ ] **Step 1: Write failing translation and job-state tests**

```python
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from sau_desktop_service import JobManager, PublishRequest, build_publish_argv


class DesktopServiceTests(unittest.TestCase):
    def test_xiaohongshu_defaults_to_headed_manual_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "demo.mp4"
            media.write_bytes(b"video")
            request = PublishRequest(
                platform="xiaohongshu", account_name="creator",
                media_file=media, title="标题",
                tags=("旅行",), content_source="original",
            )
            argv = build_publish_argv(request)
        self.assertIn("--headed", argv)
        self.assertNotIn("--automatic-publish", argv)
        self.assertEqual(argv[:2], ["xiaohongshu", "upload-video"])

    def test_douyin_requires_explicit_declaration_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "demo.mp4"
            media.write_bytes(b"video")
            request = PublishRequest(
                platform="douyin", account_name="creator",
                media_file=media, title="标题",
            )
            with self.assertRaisesRegex(ValueError, "declaration"):
                build_publish_argv(request)

    def test_job_manager_reports_failure_without_success_coercion(self):
        async def failing_dispatch(_args):
            raise RuntimeError("blocked by safety state")
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "demo.mp4"
            media.write_bytes(b"video")
            manager = JobManager(dispatcher=failing_dispatch)
            job = manager.submit(PublishRequest(
                platform="kuaishou", account_name="creator",
                media_file=media, title="标题",
            ))
            manager.wait(job.id, timeout=2)
            result = manager.get(job.id)
        self.assertEqual(result.status.value, "blocked")
        self.assertIn("safety state", result.message)
```

- [ ] **Step 2: Run the tests and verify failure**

Run: `.venv/bin/python -m unittest tests.test_desktop_service -v`

Expected: FAIL because `sau_desktop_service` does not exist.

- [ ] **Step 3: Implement typed requests, CLI translation, and bounded jobs**

Use immutable request data and an explicit state enum:

```python
class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_FOR_LOGIN = "waiting-for-login"
    WAITING_FOR_CONFIRMATION = "waiting-for-confirmation"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"

@dataclass(frozen=True, slots=True)
class PublishRequest:
    platform: Literal["douyin", "kuaishou", "tencent", "xiaohongshu"]
    account_name: str
    media_file: Path
    title: str
    tags: tuple[str, ...] = ()
    description: str = ""
    schedule: str | None = None
    declaration: str | None = None
    content_source: str | None = None
    automatic_publish: bool = False
```

`build_publish_argv()` must validate that media is inside the runtime media directory or is an existing user-selected absolute file, preserve the CLI's safe headed/manual defaults, cap Xiaohongshu tags at 10, and add `--automatic-publish` only when the request explicitly opts in.

`JobManager` uses a bounded `ThreadPoolExecutor(max_workers=2)`, stores at most 200 completed jobs, and calls the actual CLI parser/dispatcher. Map known `PublishGuard`/cooldown/dedup/corrupt-state failures to `BLOCKED`; map unclassified exceptions to `FAILED`; only a zero dispatcher result becomes `SUCCEEDED`.

- [ ] **Step 4: Replace new GUI publishing with the shared path**

Keep `/postVideo` and `/postVideoBatch` returning 410 for Douyin/Xiaohongshu so no legacy path is reopened. Add the new desktop API in Task 4 and ensure it calls only `JobManager`; do not call `myUtils.postVideo` from the new route.

- [ ] **Step 5: Run service and existing governance tests**

Run: `.venv/bin/python -m unittest tests.test_desktop_service tests.test_legacy_web_governance tests.test_publish_governance -v`

Expected: PASS.

- [ ] **Step 6: Checkpoint commit, only if explicitly authorized**

```bash
git add sau_desktop_service.py sau_backend.py tests/test_desktop_service.py
git commit -m "feat: route GUI jobs through governed CLI services"
```

### Task 4: Token-Protected Loopback Desktop API

**Files:**
- Create: `sau_desktop_api.py`
- Create: `tests/test_desktop_api.py`
- Modify: `sau_backend.py`

**Interfaces:**
- Consumes: `RuntimePaths`, `JobManager`.
- Produces: `create_desktop_app(paths, session_token, jobs) -> Flask`.
- Consumers: desktop launcher and API contract tests.

- [ ] **Step 1: Write failing API security tests**

```python
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import os

from sau_desktop_api import create_desktop_app
from sau_runtime import get_runtime_paths


class DesktopApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        with patch.dict(os.environ, {"SOCIAL_AUTO_UPLOAD_HOME": self.temp.name}):
            paths = get_runtime_paths()
        self.jobs = Mock()
        self.app = create_desktop_app(paths=paths, session_token="secret", jobs=self.jobs)
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp.cleanup()

    def test_index_sets_strict_session_cookie(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        header = response.headers["Set-Cookie"]
        self.assertIn("HttpOnly", header)
        self.assertIn("SameSite=Strict", header)

    def test_mutation_without_cookie_is_forbidden(self):
        response = self.client.post("/api/v1/publish", json={})
        self.assertEqual(response.status_code, 403)
        self.jobs.submit.assert_not_called()

    def test_publish_with_cookie_calls_job_manager(self):
        self.client.set_cookie("sau_session", "secret")
        self.jobs.submit.return_value = Mock(id="job-1", status=Mock(value="queued"))
        response = self.client.post("/api/v1/publish", json={
            "platform": "kuaishou", "accountName": "creator",
            "mediaFile": "/tmp/demo.mp4", "title": "标题",
        })
        self.assertEqual(response.status_code, 202)
        self.jobs.submit.assert_called_once()
```

- [ ] **Step 2: Run the tests and verify failure**

Run: `.venv/bin/python -m unittest tests.test_desktop_api -v`

Expected: FAIL because `sau_desktop_api` does not exist.

- [ ] **Step 3: Implement the app factory and same-origin security**

`create_desktop_app()` serves `resource_root / "frontend"` and uses `secrets.compare_digest()` for the session cookie. Require the token for every state-changing request, reject a present `Origin` that does not match the current loopback origin, set `MAX_CONTENT_LENGTH` to 2 GiB for local media, and never enable wildcard CORS.

Required endpoints:

```text
GET    /api/v1/health
GET    /api/v1/jobs/<job_id>
POST   /api/v1/publish
POST   /api/v1/login
GET    /api/v1/login/<job_id>/events
GET    /api/v1/safety/status
```

Return JSON in one shape:

```json
{"ok": true, "data": {}, "error": null}
```

or

```json
{"ok": false, "data": null, "error": {"code": "blocked", "message": "sanitized text"}}
```

Initialize SQLite tables in `paths.database_file` without importing the destructive standalone `db/createTable.py` script. Resolve all material and cookie paths beneath their designated runtime directories before file access.

- [ ] **Step 4: Make `sau_backend.py` a compatibility entry point**

Expose `app` for existing tests, but production desktop startup must call `create_desktop_app()` with a random token. Remove the executable `app.run(host="0.0.0.0", port=5409)` path; developer source mode may bind only `127.0.0.1`.

- [ ] **Step 5: Run API and legacy-web tests**

Run: `.venv/bin/python -m unittest tests.test_desktop_api tests.test_legacy_web_governance -v`

Expected: PASS and no socket is opened during import.

- [ ] **Step 6: Checkpoint commit, only if explicitly authorized**

```bash
git add sau_desktop_api.py sau_backend.py tests/test_desktop_api.py
git commit -m "feat: add private loopback desktop API"
```

### Task 5: Same-Origin Vue Desktop Integration

**Files:**
- Modify: `sau_frontend/src/utils/request.js`
- Modify: `sau_frontend/src/views/PublishCenter.vue`
- Modify: `sau_frontend/src/views/AccountManagement.vue`
- Modify: `sau_frontend/src/App.vue`
- Test: `tests/test_desktop_api.py`

**Interfaces:**
- Consumes: `/api/v1/*` contract from Task 4.
- Produces: production Vue assets under `sau_frontend/dist`.
- Consumers: desktop API static serving and PyInstaller resources.

- [ ] **Step 1: Add source-level contract assertions**

Extend `tests/test_desktop_api.py`:

```python
def test_frontend_uses_same_origin_and_new_publish_api(self):
    request_source = Path("sau_frontend/src/utils/request.js").read_text(encoding="utf-8")
    publish_source = Path("sau_frontend/src/views/PublishCenter.vue").read_text(encoding="utf-8")
    account_source = Path("sau_frontend/src/views/AccountManagement.vue").read_text(encoding="utf-8")
    self.assertNotIn("http://localhost:5409", request_source)
    self.assertIn("baseURL: '/'", request_source)
    self.assertIn("/api/v1/publish", publish_source)
    self.assertNotIn("http://localhost:5409", account_source)
```

- [ ] **Step 2: Run the assertion and verify current hard-coded URLs fail**

Run: `.venv/bin/python -m unittest tests.test_desktop_api.DesktopApiTests.test_frontend_uses_same_origin_and_new_publish_api -v`

Expected: FAIL because the current frontend contains `http://localhost:5409`.

- [ ] **Step 3: Update same-origin requests and job rendering**

Set Axios `baseURL: '/'` and `withCredentials: true`. Replace the publish call with `/api/v1/publish`, retain `publishing=true` until polling reaches a terminal state, and map `queued`, `running`, `waiting-for-login`, `waiting-for-confirmation`, `succeeded`, `failed`, and `blocked` to distinct Chinese messages. Do not label submission as publication success.

Use relative URLs for EventSource, downloads, uploads, and previews. The browser automatically sends the strict session cookie.

- [ ] **Step 4: Add the cross-computer warning**

Add a persistent warning near the GUI header:

```text
同一账号不要在多台电脑同时发布。本机发布锁不能协调其他电脑。
```

Do not add a daily limit, forced headed mode, or hidden automatic-publish opt-in.

- [ ] **Step 5: Build production assets**

Run: `npm ci && npm run build`

Workdir: `sau_frontend`

Expected: Vite exits 0 and creates `sau_frontend/dist/index.html` plus hashed assets without external CDN references.

- [ ] **Step 6: Run API tests and inspect built URLs**

Run: `.venv/bin/python -m unittest tests.test_desktop_api -v`

Run: `rg -n "localhost:5409|https://unpkg|https://cdn" sau_frontend/dist`

Expected: tests PASS; `rg` prints no matches.

- [ ] **Step 7: Checkpoint commit, only if explicitly authorized**

```bash
git add sau_frontend/src sau_frontend/package-lock.json tests/test_desktop_api.py
git commit -m "feat: connect Vue GUI to private desktop API"
```

### Task 6: Desktop Launcher and Lifecycle

**Files:**
- Create: `sau_desktop.py`
- Create: `tests/test_desktop_launcher.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: `create_desktop_app()`, `get_runtime_paths()`, `configure_browser_environment()`.
- Produces: `main()`, `start_loopback_server()`, `open_desktop_window()`.
- Consumers: PyInstaller GUI executable and source-mode smoke tests.

- [ ] **Step 1: Write failing lifecycle tests**

```python
import unittest
from unittest.mock import Mock, patch

import sau_desktop


class DesktopLauncherTests(unittest.TestCase):
    def test_server_binds_loopback_and_ephemeral_port(self):
        app = Mock()
        with patch("sau_desktop.make_server") as make_server:
            make_server.return_value.server_port = 49152
            server = sau_desktop.start_loopback_server(app)
        make_server.assert_called_once_with("127.0.0.1", 0, app, threaded=True)
        self.assertEqual(server.url, "http://127.0.0.1:49152/")

    def test_webview_failure_falls_back_to_default_browser(self):
        with patch("sau_desktop.webview.create_window", side_effect=RuntimeError("missing")), \
             patch("sau_desktop.webbrowser.open") as open_browser:
            mode = sau_desktop.open_desktop_window("http://127.0.0.1:49152/")
        self.assertEqual(mode, "browser")
        open_browser.assert_called_once()

    def test_shutdown_is_called_even_when_window_fails(self):
        server = Mock(url="http://127.0.0.1:49152/")
        with patch("sau_desktop.start_loopback_server", return_value=server), \
             patch("sau_desktop.open_desktop_window", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                sau_desktop.main()
        server.shutdown.assert_called_once()
```

- [ ] **Step 2: Run the tests and verify failure**

Run: `.venv/bin/python -m unittest tests.test_desktop_launcher -v`

Expected: FAIL because `sau_desktop` does not exist.

- [ ] **Step 3: Implement controlled startup and shutdown**

Use `werkzeug.serving.make_server("127.0.0.1", 0, app, threaded=True)`, start `serve_forever` on one daemon thread, and keep an explicit `shutdown()` handle. Generate `secrets.token_urlsafe(32)` per process. Configure the bundled browser before importing `sau_cli` or the desktop service. Open PyWebView at 1200x800; on import/runtime failure, log the sanitized reason and call `webbrowser.open(url)`.

Add entry point:

```toml
[project.gui-scripts]
sau-desktop = "sau_desktop:main"
```

- [ ] **Step 4: Run launcher and runtime tests**

Run: `.venv/bin/python -m unittest tests.test_desktop_launcher tests.test_desktop_api tests.test_runtime_paths tests.test_browser_runtime -v`

Expected: PASS without opening a real window or browser.

- [ ] **Step 5: Run a manual source-mode smoke test**

Run: `SOCIAL_AUTO_UPLOAD_HOME="$(mktemp -d)" .venv/bin/python -m sau_desktop`

Expected: a local GUI window or browser tab opens, the server address is `127.0.0.1`, and closing the window stops the server. Do not log the session token.

- [ ] **Step 6: Checkpoint commit, only if explicitly authorized**

```bash
git add sau_desktop.py pyproject.toml tests/test_desktop_launcher.py
git commit -m "feat: add desktop GUI launcher"
```

### Task 7: Frozen Payload, Clean Browser Staging, and Release Gate

**Files:**
- Create: `packaging/pyinstaller/social_auto_upload.spec`
- Create: `release_tools/__init__.py`
- Create: `release_tools/stage_browser.py`
- Create: `release_tools/verify_release.py`
- Create: `tests/test_release_packaging.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: compiled Vue `dist`, clean Chromium distribution, Python modules.
- Produces: target payload directory, `browser-manifest.json`, `release-manifest.json`, `SHA256SUMS`.
- Consumers: macOS and Windows installer tasks.

- [ ] **Step 1: Write failing release-gate tests**

```python
import subprocess
import tempfile
import unittest
from pathlib import Path

from release_tools.verify_release import ReleaseVerificationError, verify_payload


class ReleasePackagingTests(unittest.TestCase):
    def test_rejects_cookie_and_profile_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "cookies").mkdir()
            (root / "cookies/account.json").write_text("{}", encoding="utf-8")
            with self.assertRaises(ReleaseVerificationError):
                verify_payload(root, expected_platform="darwin", expected_arch="x86_64")

    def test_rejects_absolute_development_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config.txt").write_text("/Users/laverneliu/private", encoding="utf-8")
            with self.assertRaises(ReleaseVerificationError):
                verify_payload(root, expected_platform="darwin", expected_arch="x86_64")
```

- [ ] **Step 2: Run the tests and verify failure**

Run: `.venv/bin/python -m unittest tests.test_release_packaging -v`

Expected: FAIL because the release verifier does not exist.

- [ ] **Step 3: Implement clean browser staging**

`stage_browser.py` accepts `--source`, `--target`, `--platform`, `--arch`, and `--revision`; copies only the browser distribution, rejects profile/cache state such as `Default`, `Local State`, `Cookies`, `History`, and `Preferences`, locates the executable, computes SHA-256, and writes the exact manifest consumed by Task 2.

- [ ] **Step 4: Implement the release verifier**

Walk every staged file without following symlinks outside the root. Reject forbidden path components (`cookies`, `cookiesFile`, `profiles`, `.sau_safety`, `videoFile`, `logs`), known secret names (`.env`, non-default `conf.py` values), absolute workspace paths, symlink escapes, missing frontend assets, wrong executable architecture, and missing/invalid manifests. Generate SHA-256 only after all checks pass.

- [ ] **Step 5: Create the two-entry-point PyInstaller spec**

Build one console executable named `sau` and one windowed executable named `SocialAutoUpload` from the same analysis graph. Collect uploader packages, Patchright driver resources, compiled Vue files under `frontend`, and staged browser files. Use one-folder mode so the large offline browser is not unpacked on every launch.

- [ ] **Step 6: Run verifier tests and build the Intel payload**

Run: `.venv/bin/python -m unittest tests.test_release_packaging tests.test_browser_runtime -v`

Run: `.venv/bin/pyinstaller --clean --noconfirm packaging/pyinstaller/social_auto_upload.spec`

Run: `.venv/bin/python -m release_tools.verify_release --root dist/SocialAutoUpload --platform darwin --arch x86_64`

Expected: tests PASS; PyInstaller exits 0; verifier writes manifest/checksums and exits 0.

- [ ] **Step 7: Smoke-test frozen CLI and GUI server**

Run: `dist/SocialAutoUpload/sau --help`

Run: `dist/SocialAutoUpload/sau safety status --platform xiaohongshu --account smoke --json`

Expected: CLI help exits 0; safety status returns healthy empty state without network access.

Start the GUI with a temporary `SOCIAL_AUTO_UPLOAD_HOME`, verify `/api/v1/health`, then terminate it cleanly.

- [ ] **Step 8: Checkpoint commit, only if explicitly authorized**

```bash
git add packaging release_tools pyproject.toml tests/test_release_packaging.py
git commit -m "build: add clean frozen desktop payload"
```

### Task 8: Combined Intel and Apple Silicon macOS Package

**Files:**
- Create: `packaging/macos/launcher`
- Create: `packaging/macos/build_pkg.sh`
- Extend: `tests/test_release_packaging.py`

**Interfaces:**
- Consumes: verified `macos-x86_64` and `macos-arm64` payload directories.
- Produces: `SocialAutoUpload-macOS-Universal.pkg`.
- Consumers: release workflow and Desktop delivery.

- [ ] **Step 1: Add failing launcher and package-layout tests**

```python
def test_macos_launcher_selects_exact_arch_payload(self):
    source = Path("packaging/macos/launcher").read_text(encoding="utf-8")
    self.assertIn('case "$(uname -m)"', source)
    self.assertIn("x86_64)", source)
    self.assertIn("arm64)", source)
    self.assertNotIn("eval ", source)

def test_macos_package_requires_both_verified_payloads(self):
    result = subprocess.run(
        ["bash", "packaging/macos/build_pkg.sh", "--check-inputs",
         "/missing/x86_64", "/missing/arm64"],
        text=True, capture_output=True,
    )
    self.assertNotEqual(result.returncode, 0)
    self.assertIn("both", result.stderr.lower())
```

- [ ] **Step 2: Run the tests and verify failure**

Run: `.venv/bin/python -m unittest tests.test_release_packaging -v`

Expected: FAIL because the launcher/build script is absent.

- [ ] **Step 3: Implement the architecture launcher**

Use a POSIX shell `case` on `uname -m`, quote every path, reject unknown architectures, and `exec` either `Contents/Resources/payloads/x86_64/SocialAutoUpload` or `.../arm64/SocialAutoUpload`. A sibling `sau` wrapper selects the matching CLI. Do not run Rosetta or relabel one payload as the other architecture.

- [ ] **Step 4: Implement package assembly**

`build_pkg.sh` verifies both input manifests, constructs `Social Auto Upload.app`, writes a fixed `Info.plist`, copies each payload under its architecture directory, creates the CLI wrapper, and calls `pkgbuild`/`productbuild`. Install the app under `/Applications`; install the CLI wrapper only when `/usr/local/bin` is writable through the installer context.

- [ ] **Step 5: Build and inspect on an Intel Mac**

Run: `bash packaging/macos/build_pkg.sh dist/macos-x86_64 artifacts/macos-arm64 release`

Expected: `release/SocialAutoUpload-macOS-Universal.pkg` exists.

Run: `pkgutil --check-signature release/SocialAutoUpload-macOS-Universal.pkg`

Expected: reports unsigned, matching the release scope.

Run: `pkgutil --expand-full release/SocialAutoUpload-macOS-Universal.pkg "$(mktemp -d)"`

Expected: both architecture payloads and no user-data directories are present.

- [ ] **Step 6: Run native smoke tests on both Mac architectures**

Intel runner: install to a disposable target/root, run GUI health and `sau --help`, and launch the bundled Intel Chromium.

Apple Silicon runner: repeat and assert the selected executable reports `arm64`.

Expected: both native smoke tests PASS before marking the package verified.

- [ ] **Step 7: Checkpoint commit, only if explicitly authorized**

```bash
git add packaging/macos tests/test_release_packaging.py
git commit -m "build: add dual-architecture macOS package"
```

### Task 9: Windows x64 Offline Installer

**Files:**
- Create: `packaging/windows/SocialAutoUpload.iss`
- Create: `packaging/windows/build_installer.ps1`
- Extend: `tests/test_release_packaging.py`

**Interfaces:**
- Consumes: verified Windows x64 frozen payload.
- Produces: `SocialAutoUpload-Windows-x64-Setup.exe`.
- Consumers: Windows smoke job and Desktop delivery.

- [ ] **Step 1: Add failing installer-contract tests**

```python
def test_windows_installer_is_current_user_and_offline(self):
    source = Path("packaging/windows/SocialAutoUpload.iss").read_text(encoding="utf-8")
    self.assertIn("PrivilegesRequired=lowest", source)
    self.assertIn("SocialAutoUpload.exe", source)
    self.assertIn("UninstallDisplayName=Social Auto Upload", source)
    self.assertNotIn("http://", source)
    self.assertNotIn("https://", source)
```

- [ ] **Step 2: Run the tests and verify failure**

Run: `.venv/bin/python -m unittest tests.test_release_packaging -v`

Expected: FAIL because the Inno Setup file is absent.

- [ ] **Step 3: Implement the Inno Setup installer**

Install beneath `{localappdata}\Programs\SocialAutoUpload`, create desktop and Start Menu GUI shortcuts, create a Start Menu CLI shortcut, register uninstall metadata, preserve `%LOCALAPPDATA%\SocialAutoUpload` during uninstall, and offer an unchecked user-level PATH task. Include the entire verified payload and no download action.

- [ ] **Step 4: Implement the PowerShell build wrapper**

`build_installer.ps1` runs frontend build, clean browser staging, PyInstaller, release verification, Inno Setup compilation, SHA-256 generation, and Authenticode status reporting. Use `$ErrorActionPreference = "Stop"` and explicit resolved paths; do not modify machine-wide PATH.

- [ ] **Step 5: Build and smoke-test on Windows x64**

Run: `powershell -ExecutionPolicy Bypass -File packaging/windows/build_installer.ps1`

Expected: `release/SocialAutoUpload-Windows-x64-Setup.exe` exists and verifier passes.

Install silently for the current user into a disposable test account, run `sau.exe --help`, start GUI and query `/api/v1/health`, launch bundled Chromium, uninstall, and confirm `%LOCALAPPDATA%\SocialAutoUpload` remains.

- [ ] **Step 6: Checkpoint commit, only if explicitly authorized**

```bash
git add packaging/windows tests/test_release_packaging.py
git commit -m "build: add Windows x64 offline installer"
```

### Task 10: Native Build and Verification Workflow

**Files:**
- Create: `.github/workflows/desktop-release.yml`
- Extend: `tests/test_release_packaging.py`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: all build scripts and target-specific browser downloads.
- Produces: Intel Mac payload, ARM Mac payload, Windows installer, combined Mac package, manifests, logs, and checksums.
- Consumers: final delivery step.

- [ ] **Step 1: Add failing workflow-contract tests**

```python
def test_release_workflow_has_native_targets_and_bounded_permissions(self):
    workflow = Path(".github/workflows/desktop-release.yml").read_text(encoding="utf-8")
    self.assertIn("contents: read", workflow)
    self.assertIn("macos-13", workflow)
    self.assertIn("macos-14", workflow)
    self.assertIn("windows-latest", workflow)
    self.assertIn("timeout-minutes:", workflow)
    self.assertIn("verify_release", workflow)
    self.assertNotIn("pull_request_target", workflow)
```

If current official GitHub runner labels differ at implementation time, update the exact test strings and workflow together after verifying the official runner documentation.

- [ ] **Step 2: Run the tests and verify failure**

Run: `.venv/bin/python -m unittest tests.test_release_packaging -v`

Expected: FAIL because the release workflow is absent.

- [ ] **Step 3: Implement native jobs**

Use `workflow_dispatch` and version tags, `permissions: contents: read`, concurrency cancellation, per-job timeouts, pinned official actions, dependency caches keyed by lockfiles, and artifact retention. Jobs:

```text
frontend
macos-x86_64
macos-arm64
windows-x86_64
macos-universal-package
release-verification
```

Each native job stages the matching clean Chromium payload, builds, verifies, and runs smoke tests before upload. The final job verifies exactly two installer artifact names plus manifests/checksums.

- [ ] **Step 4: Validate YAML and CI contract locally**

Run: `.venv/bin/python - <<'PY'
from pathlib import Path
import yaml
yaml.safe_load(Path(".github/workflows/desktop-release.yml").read_text())
print("workflow yaml ok")
PY`

Run: `.venv/bin/python -m unittest tests.test_release_packaging tests.test_governance_ci_config -v`

Expected: YAML parse succeeds and tests PASS.

- [ ] **Step 5: Run native workflow only on an authorized build surface**

Do not push upstream implicitly. When an authorized fork/remote or native machines are available, trigger the workflow, wait for every native smoke job, and download only artifacts whose verification job passed.

- [ ] **Step 6: Checkpoint commit, only if explicitly authorized**

```bash
git add .github/workflows/desktop-release.yml .gitignore tests/test_release_packaging.py
git commit -m "ci: build and verify native desktop installers"
```

### Task 11: Documentation, Full Verification, and Desktop Delivery

**Files:**
- Create: `docs/desktop-install.md`
- Modify: `README.md`
- Modify: `docs/install.md`
- Modify: `docs/CLI.md`
- Verify: all source, tests, build scripts, installers, manifests, and checksums.

**Interfaces:**
- Consumes: two verified installers and release manifests.
- Produces: operator-ready Desktop release directory and exact usage/uninstall instructions.

- [ ] **Step 1: Write the install guide with exact paths and warnings**

Document:

```text
macOS data: ~/Library/Application Support/SocialAutoUpload
Windows data: %LOCALAPPDATA%\SocialAutoUpload
macOS app: /Applications/Social Auto Upload.app
Windows app: %LOCALAPPDATA%\Programs\SocialAutoUpload
```

Include unsigned Gatekeeper/SmartScreen instructions, GUI launch, CLI examples, offline-browser verification, uninstall, explicit optional data cleanup, and the warning:

```text
同一账号不要在多台电脑同时发布。本机发布锁不能协调其他电脑。
```

State that packaging lowers installation friction but does not guarantee avoidance of platform detection or account restrictions.

- [ ] **Step 2: Run the complete Python and frontend verification**

Run: `.venv/bin/python -m unittest discover -s tests -v`

Expected: all tests PASS.

Run: `.venv/bin/python -m compileall -q sau_cli.py sau_runtime.py sau_browser_runtime.py sau_desktop_service.py sau_desktop_api.py sau_desktop.py uploader utils myUtils release_tools`

Expected: exit 0.

Run: `npm ci && npm run build`

Workdir: `sau_frontend`

Expected: exit 0.

Run: `git diff --check`

Expected: no whitespace errors.

- [ ] **Step 3: Verify final artifacts and checksums**

Run the release verifier against both installer payloads, then generate:

```bash
shasum -a 256 \
  release/SocialAutoUpload-macOS-Universal.pkg \
  release/SocialAutoUpload-Windows-x64-Setup.exe \
  > release/SHA256SUMS
shasum -a 256 -c release/SHA256SUMS
```

Expected: both files report `OK`. On Windows, independently run `Get-FileHash -Algorithm SHA256` and compare it to the manifest.

- [ ] **Step 4: Copy verified delivery files to the Desktop**

Create `~/Desktop/SocialAutoUpload-Offline-Installers/` and copy only:

```text
SocialAutoUpload-macOS-Universal.pkg
SocialAutoUpload-Windows-x64-Setup.exe
SHA256SUMS
release-manifest.json
安装说明.md
```

Do not copy source cookies, profiles, logs, videos, audit/evidence state, or development configuration.

- [ ] **Step 5: Final evidence report**

Record native runner OS/architecture, application version, browser revision, installer sizes, SHA-256 values, test counts, unsigned status, and known limitations. Link the two Desktop installers and the install guide in the final response. Do not claim completion if either native smoke test or checksum verification is missing.

- [ ] **Step 6: Final checkpoint commit, only if explicitly authorized**

```bash
git add README.md docs/install.md docs/CLI.md docs/desktop-install.md
git commit -m "docs: add offline desktop installation guide"
```
