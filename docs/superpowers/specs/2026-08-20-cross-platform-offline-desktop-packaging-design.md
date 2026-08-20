# Cross-Platform Offline Desktop Packaging Design

## Status

Approved in chat on 2026-08-20. This design packages the existing application
as an offline desktop product without changing the Phase 3C publishing-policy
decisions.

## Goals

1. Deliver one macOS installer that runs on Intel (`x86_64`) and Apple Silicon
   (`arm64`) Macs, plus one Windows 10/11 x64 installer.
2. Make the existing graphical interface the normal entry point while keeping
   the complete `sau` command-line interface available for scripting.
3. Run after installation without Codex, a source checkout, a system Python,
   Node.js, or a first-run browser download.
4. Keep GUI and CLI publishing behavior behind the same application services
   and the same governance controls.
5. Never distribute account cookies, browser profiles, audit history, local
   governance state, uploaded media, or other machine-specific user data.

## Non-goals

- This release does not add CAPTCHA solving, browser-fingerprint spoofing,
  proxy rotation, challenge bypass, or other platform-control evasion.
- It does not promise that a platform will accept a login or publication, or
  that an account cannot be restricted.
- It does not coordinate publishing locks across different computers.
- It does not expand the first GUI release beyond the platforms and operations
  already represented by the current Vue GUI. Other existing platforms remain
  available through the CLI.
- It does not change the accepted Phase 3C decisions: no daily quota, no forced
  headed mode, no removal of automatic publishing, and no new time windows.
- The first artifacts are unsigned. Apple notarization and Windows Authenticode
  signing are separate release steps that require operator-provided credentials.

## Chosen Approach

Reuse the existing Vue interface and Flask application rather than rebuilding
all screens in PySide6 or shipping Electron. A desktop launcher starts a private
loopback application server and opens the GUI in a native system WebView. When
the WebView runtime is unavailable, the launcher opens the same local GUI in the
default browser and reports that fallback in the application log.

This approach keeps the current GUI, avoids a second application model, and
does not add Electron's extra Chromium runtime. A PySide6 rewrite would duplicate
existing forms and account flows. An Electron wrapper would increase artifact
size and add another JavaScript/native packaging boundary without improving the
publishing core.

## Architecture

### 1. Application services

Extract the reusable account, login, validation, upload, and safety-status
operations behind a small Python service layer. The CLI dispatcher and the
local GUI API call that layer. The GUI must not create an alternate direct path
around `PublishGuard`, publish permits, cooldown checks, exact-content
deduplication, failure evidence, or audit logging.

The first refactor is intentionally surgical: retain existing uploader classes
and CLI request dataclasses, move only the orchestration required for both entry
points, and keep current output and exit-code behavior stable.

### 2. Desktop launcher and local API

The GUI executable performs these steps:

1. Resolve packaged resources and the per-user data directory.
2. Configure the bundled Patchright/Chromium location before uploader imports.
3. Generate an in-memory session token.
4. Bind the Flask server to `127.0.0.1` on an available ephemeral port.
5. Serve the compiled Vue assets and API from the same origin.
6. Require the session token on state-changing API calls.
7. Open a desktop WebView; fall back to the default browser when necessary.
8. Shut down the local server on normal application exit.

The server never binds to `0.0.0.0`, does not expose a LAN mode, and does not
persist the session token. Existing endpoints that mutate files or publishing
state receive path validation, request validation, and the same governance
checks as their CLI equivalents.

### 3. Resource and user-data separation

Packaged code, compiled Vue assets, icons, templates, and browser binaries are
read-only resources. Runtime writes use platform-standard per-user locations:

- macOS: `~/Library/Application Support/SocialAutoUpload`
- Windows: `%LOCALAPPDATA%\SocialAutoUpload`

The data root contains `cookies`, browser profiles, logs, safety state, failure
evidence, configuration overrides, and GUI working data. Directory creation is
centralized. Sensitive files retain restrictive POSIX modes where supported.
Windows relies on the current user's local application-data ACL.

Existing source-checkout behavior remains compatible for developers, but a
frozen application must never derive writable paths from `/Applications`,
`Program Files`, `sys._MEIPASS`, or another packaged-resource directory.

### 4. Offline browser payload

Each target payload contains the exact Patchright-compatible Chromium revision
for its operating system and architecture. Startup resolves the bundled browser
explicitly and fails with a clear local diagnostic if its executable is missing
or corrupt. The application does not silently download a replacement.

The build records browser revision, executable path, file size, and SHA-256 in
the release manifest. Installation never includes the current development
machine's browser profile or Playwright cache wholesale; only the required clean
browser distribution is copied.

### 5. GUI and CLI entry points

- macOS installs `Social Auto Upload.app` and a `sau` command wrapper. The app
  launches the GUI; the wrapper selects the native packaged CLI payload.
- Windows installs a desktop/Start Menu GUI shortcut and a `Social Auto Upload
  Command Line` shortcut. The installer offers a non-default option to add the
  CLI directory to the user's `PATH`.

Both entry points use the same version, configuration, data root, browser
locator, service layer, and governance code. A GUI job reports queued, running,
waiting-for-login, waiting-for-confirmation, succeeded, failed, or blocked state
without converting an unknown result into success.

## Packaging and Release Layout

### macOS

Build frozen payloads on native Intel and Apple Silicon runners. The final
`.app` contains architecture-specific internal payloads selected by a small
launcher using the runtime architecture. `productbuild` assembles one `.pkg`
that installs the application and CLI wrapper. Nested payloads keep separate
Chromium binaries and native Python extensions; they are not renamed or claimed
to be universal binaries.

The unsigned package must be installable through the documented macOS override
flow. A future signed release signs nested executables from the inside out,
signs the package, submits it for notarization, and staples the result.

### Windows

Build the frozen x64 payload on a native Windows runner. An installer generator
creates one offline `.exe` with application files, Chromium, shortcuts,
uninstall metadata, and the optional user-level `PATH` update. The installer
does not require administrator access when installed for the current user.

The first unsigned build will trigger SmartScreen on some machines. A future
release can sign the application and installer with the operator's certificate.

### Build reproducibility

The repository contains deterministic build entry points for frontend assets,
Python freezing, browser staging, installer assembly, manifest creation, and
checksums. CI uses separate native target jobs and uploads target artifacts. A
final macOS assembly job downloads both Mac payloads and builds the combined
package. Build scripts reject a dirty browser payload, missing frontend assets,
unexpected user-data files, or an architecture mismatch.

## Data Flow

For GUI publication, the user selects an account and media in Vue. The local API
validates the request and converts it to the same request model used by the CLI.
The application service acquires the existing governance permit, invokes the
uploader, records bounded audit/evidence data, and returns a structured result.
The API streams or polls job status; the GUI renders the state and a sanitized
error. CLI publication follows the same service path and maps the result to text
and an exit code.

Login opens the bundled headed browser on the local computer. Each computer
creates and stores its own account state. The installer never migrates or shares
cookies automatically. The GUI displays a warning that the same account must
not be published concurrently from multiple computers because local locks do
not provide distributed coordination.

## Error Handling and Recovery

- Missing/corrupt packaged resources stop startup before any platform action.
- Port binding retries on a new ephemeral port; it never falls back to a public
  network interface.
- GUI API validation failures return structured client errors and do not launch
  a browser.
- Browser challenge pages, HTTP failures, upload deadlines, corrupt safety
  state, and uncertain publish results remain fail-closed.
- A GUI crash does not delete cookies, logs, or governance state. A stale local
  job is surfaced on the next start using existing state and lock diagnostics.
- The uninstaller removes packaged files and shortcuts. Per-user account data is
  preserved by default and can be removed only through a separate explicit
  cleanup action documented with its exact path.

## Security and Privacy

The release pipeline scans staged payloads and installers for `cookies`, browser
profiles, upload media, audit/evidence records, known secret-file patterns, and
absolute development-machine paths. Any match fails the build. Logs and GUI
errors redact cookie values, authorization data, tokens, and URL query/fragment
content. The loopback token protects state-changing local API calls from
unrelated browser pages; normal filesystem permissions protect local account
state.

Packaging does not alter browser identity to defeat platform checks. Risk
remains controlled by existing fail-closed publishing checks and operator usage,
not by the GUI or installer format.

## Verification

1. Existing unit, CLI, uploader, governance, spawn-lock, and legacy-web tests
   remain green.
2. New tests cover platform data paths, frozen resource paths, browser
   resolution, token enforcement, loopback-only binding, GUI-to-service request
   mapping, and server shutdown.
3. Frontend production build and API contract tests pass.
4. Each native payload passes `--version`, CLI help, safety-status, GUI startup,
   clean shutdown, and bundled-browser launch smoke tests.
5. macOS Intel, macOS Apple Silicon, and Windows x64 run on clean target
   machines without Python, Node.js, Codex, or network browser downloads.
6. Installer contents and a fresh installation are scanned to confirm that no
   cookie, browser profile, media file, audit record, user-specific path, or
   development secret is present.
7. Release output contains the two requested installers, a manifest, SHA-256
   checksums, installation instructions, uninstall instructions, and the known
   unsigned-package warnings.

## Acceptance Criteria

- Double-clicking the installed application opens a usable GUI and does not
  require Codex or development tools.
- `sau` remains usable for every command supported before packaging.
- The application can open the bundled browser while offline.
- GUI and CLI cannot bypass the mandatory governance controls.
- The macOS package chooses a native Intel or Apple Silicon payload correctly.
- The Windows package installs, launches, and uninstalls for the current user.
- No user/account data is present in either installer.
- The two installer files and their verification artifacts are copied to the
  operator's Desktop after successful target verification.

## Delivery Constraints

The current workstation is an Intel Mac, so it can verify the Intel macOS
payload locally. Native Apple Silicon and Windows verification requires an
Apple Silicon runner/machine and a Windows x64 runner/machine. The build system
will produce those artifacts only on matching targets; it must not label an
untested cross-built payload as verified. Running remote CI or copying artifacts
from the operator's other computers requires access to those build surfaces.
