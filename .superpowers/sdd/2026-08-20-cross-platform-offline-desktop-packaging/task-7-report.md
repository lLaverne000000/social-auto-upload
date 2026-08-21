# Task 7 implementation report

## Result

Implemented clean Chromium staging, a fail-closed release verifier, and a
shared-analysis PyInstaller one-folder payload with console `sau` and windowed
`SocialAutoUpload` executables. A verified Intel macOS payload remains locally
at `dist/SocialAutoUpload`; the 340 MB staging tree and 973 MB frozen payload
are ignored and were not committed.

## TDD and tests

- Initial RED: `tests.test_release_packaging` failed with
  `ModuleNotFoundError: release_tools`.
- Spec RED: the dispatcher and spec were absent; the two directed tests errored.
- Chromium COLLECT regression RED proved browser Mach-O entries must be moved
  back from PyInstaller `BINARY` to opaque `DATA` entries.
- Final focused suite: 24 tests passed.
- Final full suite using the requested shared environment:
  `.venv/bin/python -m unittest discover -s tests -v` — 225 tests passed.
- `compileall` and `git diff --check` passed.

## Build environment and browser staging

- Python 3.12.14, PyInstaller 6.22.2, PyWebView 5.4, Patchright 1.58.2,
  Flask 3.1.1, OpenCV distribution 4.12.0.88.
- OpenCV is exactly pinned to 4.12.0.88 because 4.13.0.92 provides only a
  `macosx_14_0_x86_64` wheel and is unsatisfiable on the Intel macOS 13 builder.
- Bundled Node 24.19.0 plus `pnpm dlx npm@11.6.2 ci` installed 106 packages
  with 0 audit vulnerabilities; four frontend tests and the Vite build passed.
- Staged Patchright Chromium revision 1208 / Chrome for Testing 145.0.7632.6.
- Staged browser size: 340 MB; architecture: x86_64; five contained framework
  symlinks preserved.
- Browser executable SHA-256:
  `b585211807f14d4e7b03572a0a81506a317c19902ccb01464eda595416f1f7e8`.

## Frozen payload and release gate

- Clean PyInstaller build completed successfully in 41.155 seconds.
- Payload: `dist/SocialAutoUpload`, 973 MB, both executables x86_64.
- Release verifier passed and generated stable metadata only after validation:
  schema 1, darwin/x86_64, browser revision 1208, 995 files,
  1,017,443,628 bytes.
- `release-manifest.json` SHA-256:
  `f5705e77d3ed66abf8f07131618b6602cb46ea910c1e5a00d66d9bd175c86e27`.
- `SHA256SUMS` SHA-256:
  `8b9e018f61365d954959aab81805e7d53572cd1920d4e8a31e5796b139bcdf27`.
- Re-running the verifier reproduced both hashes exactly.
- The gate rejects runtime/profile/media/log/database paths, secrets and
  private keys, unsafe config values, development paths, invalid/missing
  frontend or browser manifests, hash/architecture mismatch, sockets, and
  escaping symlinks. Public CA bundles remain allowed.

## Frozen smoke evidence

- `sau --help`: exit 0.
- Offline `sau safety status ... --json`: exit 0, missing/healthy empty state,
  no publication or network action, runtime files confined to a temporary
  `SOCIAL_AUTO_UPLOAD_HOME`.
- Patchright launched the manifest-selected bundled browser, loaded a `data:`
  page, read title/content, and closed cleanly; no browser/driver process
  remained.
- Frozen GUI bound an ephemeral literal `127.0.0.1` port; index and health were
  HTTP 200; a session-cookie plus same-origin protected quit returned HTTP 202;
  the GUI exited 0 with no session token/name in captured output and no
  remaining GUI/server process.

## Changed files

- `.gitignore`
- `packaging/pyinstaller/social_auto_upload.spec`
- `release_tools/__init__.py`
- `release_tools/stage_browser.py`
- `release_tools/verify_release.py`
- `sau_frozen_entry.py`
- `tests/test_release_packaging.py`
- `pyproject.toml`
- `uv.lock`

## Concerns

- The current Chrome for Testing distribution is not upstream code-signed, but
  Patchright launch succeeds. Task 8 must package/sign the containing installer
  without mutating the verified browser payload.
- This task produced only the native Intel macOS payload. Windows and Apple
  Silicon payloads still require their native Task 9/10 build surfaces.

## Fix round 1

All four Important review findings were reproduced before implementation.
The directed RED run produced 23 assertion failures and two errors: secret and
private-key browser sources were accepted, FIFO reached `shutil.copytree`,
hardlinks and post-stage profile state were ignored, a missing/symlink GUI
entry point passed, unknown `conf.py` AST was ignored, checksum-unsafe names
were emitted, and the release manifest had no symlink inventory.

The hardened staging gate now rejects `.env`, SSH keys, credential/secret
files, private-key suffixes/content, hardlinks, FIFOs, sockets, devices, and
every filesystem entry other than contained symlinks, directories, and
single-link regular files. The retained clean Chromium source passes this
stricter scan.

The release gate now:

- rejects browser `Default`, `Local State`, `History`, `Preferences`, and the
  existing runtime/profile state set at any depth;
- rejects hardlinks, FIFO/socket/device/special entries before metadata output;
- requires both target entry points as executable, non-symlink, single-link
  regular files with the requested architecture;
- accepts `conf.py` only when its AST exactly matches the controlled runtime
  boilerplate and five safe literal settings;
- rejects CR/LF/control/backslash names and unsafe symlink targets; and
- emits schema 2 with a deterministic path/target/SHA-256 record for every
  contained file or directory symlink, while `SHA256SUMS` remains a standard
  regular-file list without recursive metadata checksums.

Verification after the fix:

- Directed review tests: 10 passed.
- Focused Task 2/7 tests: 34 passed.
- Full suite: 235 passed in 11.868 seconds.
- `compileall`, controlled source `conf.py` comparison, and `git diff --check`
  passed.
- The retained 973 MB payload passed twice without a rebuild: both x86_64
  entry points are regular/non-symlink, all 79 symlinks are covered, and both
  metadata files were byte-stable.
- Schema 2 `release-manifest.json` SHA-256:
  `44f73660f62c00ca6d5e3fbf1a7712ed65ffe1a0b06cb3303ba13d4009ce8a59`.
- Stable `SHA256SUMS` SHA-256:
  `8b9e018f61365d954959aab81805e7d53572cd192f0d4e8a31e5796b139bcdf27`.
