#!/bin/bash
set -euo pipefail
umask 022

SCRIPT_DIR=$(CDPATH= cd -P -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
PROJECT_ROOT=$(CDPATH= cd -P -- "${SCRIPT_DIR}/../.." && pwd -P)
PYTHON_BIN=${SAU_PYTHON:-"${PROJECT_ROOT}/.venv/bin/python"}
PACKAGE_NAME="SocialAutoUpload-macOS-Universal.pkg"

die() {
    printf 'Social Auto Upload macOS package: %s\n' "$*" >&2
    exit 1
}

usage() {
    printf 'Usage:\n' >&2
    printf '  %s --check-inputs <macos-x86_64-payload> <macos-arm64-payload>\n' "$0" >&2
    printf '  %s <macos-x86_64-payload> <macos-arm64-payload> <output-directory>\n' "$0" >&2
    exit 64
}

verify_one_payload() {
    local payload=$1
    local arch=$2
    local verifier_output

    if ! verifier_output=$(PYTHONPATH="${PROJECT_ROOT}${PYTHONPATH:+:${PYTHONPATH}}" \
        "$PYTHON_BIN" -m release_tools.verify_release \
        --root "$payload" --platform darwin --arch "$arch" 2>&1); then
        printf 'Payload architecture verification failed for darwin/%s: %s\n' \
            "$arch" "$verifier_output" >&2
        return 1
    fi
    PYTHONPATH="${PROJECT_ROOT}${PYTHONPATH:+:${PYTHONPATH}}" \
        "$PYTHON_BIN" - "$payload/release-manifest.json" "$arch" <<'PY'
import json
import sys
from pathlib import Path

manifest_path = Path(sys.argv[1])
expected_arch = sys.argv[2]
try:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
except (OSError, UnicodeError, json.JSONDecodeError) as exc:
    raise SystemExit(f"invalid verified release manifest: {manifest_path}: {exc}")
if manifest.get("platform") != "darwin" or manifest.get("arch") != expected_arch:
    raise SystemExit(
        f"declared payload architecture mismatch: expected darwin/{expected_arch}, "
        f"found {manifest.get('platform')}/{manifest.get('arch')}"
    )
PY
}

verify_both_payloads() {
    local x86_payload=$1
    local arm_payload=$2

    if [[ ! -d "$x86_payload" || ! -d "$arm_payload" ]]; then
        die "both verified payload directories are required (x86_64 and arm64)"
    fi
    if [[ -L "$x86_payload" || -L "$arm_payload" ]]; then
        die "both payload roots must be real directories, not symlinks"
    fi
    [[ -x "$PYTHON_BIN" ]] || die "release verifier Python is not executable: $PYTHON_BIN"

    verify_one_payload "$x86_payload" "x86_64"
    verify_one_payload "$arm_payload" "arm64"
    printf 'Both native macOS payloads verified (x86_64 and arm64).\n'
}

if [[ ${1:-} == "--check-inputs" ]]; then
    [[ $# -eq 3 ]] || usage
    verify_both_payloads "$2" "$3"
    exit 0
fi

[[ $# -eq 3 ]] || usage
[[ $(uname -s) == "Darwin" ]] || die "native macOS pkg assembly requires Darwin"
for tool in ditto pkgbuild productbuild; do
    command -v "$tool" >/dev/null 2>&1 || die "required macOS packaging tool is missing: $tool"
done

x86_payload=$1
arm_payload=$2
output_directory=$3
verify_both_payloads "$x86_payload" "$arm_payload"

if [[ -L "$output_directory" || ( -e "$output_directory" && ! -d "$output_directory" ) ]]; then
    die "output path must be a real directory: $output_directory"
fi
mkdir -p -- "$output_directory"
output_directory=$(CDPATH= cd -P -- "$output_directory" && pwd -P)
output_package="${output_directory}/${PACKAGE_NAME}"
if [[ -L "$output_package" || ( -e "$output_package" && ! -f "$output_package" ) ]]; then
    die "refusing to replace a symlink or non-regular output: $output_package"
fi

staging_root=$(mktemp -d "${TMPDIR:-/tmp}/sau-macos-package.XXXXXX")
output_staging_directory=""
cleanup() {
    if [[ -n ${output_staging_directory:-} && -d "$output_staging_directory" ]]; then
        rm -rf -- "$output_staging_directory"
    fi
    if [[ -n ${staging_root:-} && -d "$staging_root" ]]; then
        rm -rf -- "$staging_root"
    fi
}
trap cleanup EXIT HUP INT TERM
output_staging_directory=$(mktemp -d "${output_directory}/.SocialAutoUpload-macOS-Universal.XXXXXX")
temporary_output_package="${output_staging_directory}/${PACKAGE_NAME}"

package_root="${staging_root}/root"
app="${package_root}/Applications/Social Auto Upload.app"
macos_directory="${app}/Contents/MacOS"
payload_directory="${app}/Contents/Resources/payloads"
scripts_directory="${staging_root}/scripts"
mkdir -p -- "$macos_directory" "$payload_directory/x86_64" "$payload_directory/arm64" "$scripts_directory"

ditto --norsrc --noqtn "$x86_payload" "$payload_directory/x86_64"
ditto --norsrc --noqtn "$arm_payload" "$payload_directory/arm64"
install -m 0755 "$SCRIPT_DIR/launcher" "$macos_directory/launcher"
install -m 0755 "$SCRIPT_DIR/launcher" "$macos_directory/sau"

cat >"${app}/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleDisplayName</key>
    <string>Social Auto Upload</string>
    <key>CFBundleExecutable</key>
    <string>launcher</string>
    <key>CFBundleIdentifier</key>
    <string>com.socialautoupload.desktop</string>
    <key>CFBundleName</key>
    <string>Social Auto Upload</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>0.1.0</string>
    <key>CFBundleVersion</key>
    <string>1</string>
    <key>LSMinimumSystemVersion</key>
    <string>12.0</string>
</dict>
</plist>
PLIST

cat >"${scripts_directory}/postinstall" <<'POSTINSTALL'
#!/bin/sh
set -eu

script_directory=$(CDPATH= cd -P -- "$(dirname -- "$0")" && pwd -P)
target_volume=${3:-/}
case "$target_volume" in
    /*) ;;
    *) exit 0 ;;
esac

target_prefix=${target_volume%/}
usr_directory="${target_prefix}/usr"
local_directory="${usr_directory}/local"
cli_directory="${local_directory}/bin"
cli_link="${cli_directory}/sau"
expected_self_target="/Applications/Social Auto Upload.app/Contents/MacOS/sau"

for component in "$usr_directory" "$local_directory" "$cli_directory"; do
    if [ -L "$component" ]; then
        printf 'Social Auto Upload: skipping CLI install through symlink component %s\n' "$component" >&2
        exit 0
    fi
    if [ ! -d "$component" ]; then
        exit 0
    fi
done

if [ ! -w "$cli_directory" ]; then
    exit 0
fi
if [ -L "$cli_link" ]; then
    existing_target=$(readlink "$cli_link") || exit 0
    if [ "$existing_target" = "$expected_self_target" ]; then
        exit 0
    fi
    printf 'Social Auto Upload: preserving existing foreign symlink %s\n' "$cli_link" >&2
    exit 0
fi
if [ -e "$cli_link" ]; then
    printf 'Social Auto Upload: preserving existing non-symlink %s\n' "$cli_link" >&2
    exit 0
fi
install -m 0755 "${script_directory}/cli-wrapper" "$cli_link"
exit 0
POSTINSTALL
chmod 0755 "${scripts_directory}/postinstall"

cat >"${scripts_directory}/cli-wrapper" <<'CLI_WRAPPER'
#!/bin/sh
exec "/Applications/Social Auto Upload.app/Contents/MacOS/sau" "$@"
CLI_WRAPPER
chmod 0755 "${scripts_directory}/cli-wrapper"

component_package="${staging_root}/SocialAutoUpload-component.pkg"
pkgbuild \
    --root "$package_root" \
    --scripts "$scripts_directory" \
    --identifier "com.socialautoupload.desktop" \
    --version "0.1.0" \
    --install-location "/" \
    "$component_package"
productbuild --package "$component_package" "$temporary_output_package"

if [[ -L "$temporary_output_package" || ! -f "$temporary_output_package" ]]; then
    die "productbuild did not create a regular package: $temporary_output_package"
fi
mv -f -- "$temporary_output_package" "$output_package"
[[ -f "$output_package" && ! -L "$output_package" ]] || \
    die "atomic package replacement failed: $output_package"
printf 'Created unsigned macOS package: %s\n' "$output_package"
