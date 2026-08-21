# Task 8 implementation report

## Result

Implemented the dual-payload macOS launcher and package assembler. The app
launcher selects only the exact `x86_64` or `arm64` payload reported by
`uname -m`; a sibling `sau` launcher selects the matching CLI. The build script
re-runs the Task 7 release verifier for both declared architectures before any
package assembly and refuses missing, symlinked, swapped, or invalid inputs.

The package root is fixed at `Applications/Social Auto Upload.app`, with the
two untouched native payloads under
`Contents/Resources/payloads/{x86_64,arm64}`. A fixed `Info.plist`, GUI
launcher, and CLI launcher are installed in the app. The component package
postinstall creates `/usr/local/bin/sau` only when `/usr/local/bin` already
exists and is writable in the installer target; it preserves an existing
non-symlink. Assembly uses a private temporary root, a fixed output name, and
`umask 022` so caller umask cannot change the application layout.

## TDD evidence

- Initial RED: all seven directed `MacOSPackagingTests` failed because
  `packaging/macos/launcher` and `packaging/macos/build_pkg.sh` did not exist.
- First GREEN attempt: six passed and the swapped-architecture case failed
  only because the low-level verifier diagnostic did not contain the word
  `architecture`. The builder now emits a precise architecture-verification
  failure and all seven passed.
- Deterministic-permissions RED: running the real builder under `umask 077`
  produced app mode `0700` instead of `0755`. The builder now fixes its umask;
  the app is `0755` and `Info.plist` is `0644` in the expanded package.
- Final focused macOS tests: 7 passed.
- Final release-packaging suite: 32 passed.
- Final full suite: 242 passed in 17.475 seconds.
- `bash -n`, `sh -n`, Python compilation, and `git diff --check` passed.

## Native Intel package-tool inspection

The Intel macOS 13.5.1 host has `/usr/bin/pkgbuild`, `/usr/bin/productbuild`,
and `/usr/sbin/pkgutil`. A tiny synthetic x86_64/arm64 fixture pair passed the
real verifier, was assembled by the real native package tools, and was expanded
with `pkgutil --expand-full`. Inspection found exactly one app with both fixed
payload roots and both launchers. The conditional postinstall was executed
against disposable target roots: it created nothing when `usr/local/bin` was
absent and created only the expected `usr/local/bin/sau` wrapper when the
directory was writable.

`pkgutil --check-signature` returned exit 1 with `Status: no signature`, which
matches the unsigned release scope. This synthetic package was temporary and
was not retained or represented as a user release.

## Native release limitation

The retained verified Intel payload remains at `dist/SocialAutoUpload`, but no
real Apple Silicon payload is available on this Intel workstation. Running
`--check-inputs` with the retained Intel payload and the absent
`artifacts/macos-arm64` directory exits 1 with the required `both verified
payload directories are required` diagnostic. Therefore no real
`SocialAutoUpload-macOS-Universal.pkg` was created and no Apple Silicon native
smoke result is claimed. A real arm64 Task 7 payload and arm64 smoke runner are
still required before the combined package can be marked verified or delivered.

## Changed files

- `packaging/macos/launcher`
- `packaging/macos/build_pkg.sh`
- `tests/test_release_packaging.py`

## Fix round 1

All three review findings were reproduced before their corresponding fixes.

1. The installed CLI path was invalid when reached through a symlink. The RED
   test invoked a real temporary `usr/local/bin/sau` symlink and got exit 66
   because the launcher searched below `usr/local/Resources`; the expanded
   package also installed a symlink instead of an independent wrapper. The app
   launcher now resolves a bounded symlink chain while retaining the original
   invocation name. New installs receive a regular executable wrapper whose
   only action is the quoted fixed exec of
   `/Applications/Social Auto Upload.app/Contents/MacOS/sau`. The real
   temporary symlink test reaches the fake native CLI, preserves two arguments,
   and preserves its exit status 37.
2. The postinstall path traversal RED produced six failures: symlinks at each
   of target `usr`, `usr/local`, and `usr/local/bin` wrote outside the target;
   existing foreign, broken, and expected-self `sau` symlinks were replaced or
   errored. The script now checks each component without following it and
   skips CLI installation if any is a symlink, missing, or unwritable. Existing
   regular entries and all foreign symlinks are preserved. Only a symlink whose
   target exactly equals the expected app CLI is accepted as already installed.
3. The atomic-output RED used a fake `productbuild` that wrote a partial package
   and exited 23; it replaced the old sentinel because the official output had
   been deleted first. `productbuild` now writes a private `.pkg` inside a
   same-output-directory temporary directory. A regular, non-symlink result is
   required before same-directory atomic `mv`. Failure preserves the old
   package and removes temporary output. A second adversarial producer that
   returned success with a symlink was rejected without changing the sentinel;
   a real native `productbuild` then replaced it successfully.

Fix-round verification:

- Focused macOS tests: 10 passed.
- Release-packaging suite: 35 passed in 6.640 seconds.
- Full suite: 245 passed in 16.747 seconds.
- `bash -n`, `sh -n`, `py_compile`, and `git diff --check` passed.
- A fresh real synthetic package was a regular file, expanded with exactly one
  x86_64 entry, one arm64 entry, one CLI wrapper, and one postinstall script.
- `pkgutil --check-signature` again returned exit 1 and `Status: no signature`.

The architecture limitation is unchanged: these were synthetic packaging
fixtures. There is still no real Apple Silicon payload or Apple Silicon native
smoke result, so no formal universal installer was produced or claimed.
