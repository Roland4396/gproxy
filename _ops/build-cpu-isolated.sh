#!/usr/bin/env bash
# CPU-only fallback when hosted Actions cannot dispatch. No account data or
# provider credentials are copied. Run as the unprivileged build owner.
set -euo pipefail
umask 077
ROOT=$(git rev-parse --show-toplevel)
BUILD=${GPROXY_BUILD_ROOT:?Set a dedicated user-owned build directory}
mkdir -p "$BUILD"/{tools,downloads,logs,artifact}
export CARGO_HOME="$BUILD/tools/cargo" RUSTUP_HOME="$BUILD/tools/rustup"
export CARGO_TARGET_DIR="$BUILD/target" CARGO_BUILD_JOBS=16
export CARGO_PROFILE_DEV_DEBUG=0 CARGO_PROFILE_TEST_DEBUG=0
export CARGO_PROFILE_RELEASE_LTO=thin CARGO_PROFILE_RELEASE_CODEGEN_UNITS=8
export CUDA_VISIBLE_DEVICES='' NVIDIA_VISIBLE_DEVICES=void
export GPROXY_BUILD_VERSION=4.1.14+local.idle0 GPROXY_BUILD_CHANNEL=dev
export GPROXY_BUILD_HASH=$(git -C "$ROOT" rev-parse HEAD) GPROXY_INSTALLATION_KIND=container
export GPROXY_UPDATE_PUBKEY=$(cat "$ROOT/.gitlab/update-public-key")
fetch() {
  local url=$1 out=$2
  # Dedicated proxy only for compiler/archive downloads; fall back to direct.
  curl --fail --location --retry 2 --connect-timeout 20 --proxy http://127.0.0.1:17890 "$url" -o "$out" || curl --fail --location --retry 2 --connect-timeout 20 --noproxy '*' "$url" -o "$out"
}
if [ ! -x "$CARGO_HOME/bin/rustc" ]; then
  base=https://static.rust-lang.org/rustup/dist/x86_64-unknown-linux-gnu
  fetch "$base/rustup-init" "$BUILD/downloads/rustup-init"
  fetch "$base/rustup-init.sha256" "$BUILD/downloads/rustup-init.sha256"
  (cd "$BUILD/downloads" && sha256sum -c rustup-init.sha256)
  chmod 700 "$BUILD/downloads/rustup-init"
  HTTPS_PROXY=http://127.0.0.1:17890 HTTP_PROXY=http://127.0.0.1:17890 "$BUILD/downloads/rustup-init" -y --no-modify-path --profile minimal --default-toolchain none
fi
export PATH="$CARGO_HOME/bin:$BUILD/tools/go/bin:$BUILD/tools/node/bin:$BUILD/tools/pnpm/node_modules/.bin:/usr/bin:/bin"
# rustc is a rustup shim: its existence does not prove a toolchain finished
# installing. Explicit selection also makes interrupted bootstrap resumable.
export RUSTUP_TOOLCHAIN=1.99.0
HTTPS_PROXY=http://127.0.0.1:17890 HTTP_PROXY=http://127.0.0.1:17890 rustup toolchain install "$RUSTUP_TOOLCHAIN" --profile minimal --component rustfmt
if [ ! -x "$BUILD/tools/go/bin/go" ]; then
  # Go publishes archive checksums in its JSON index, not .tar.gz.sha256.
  fetch 'https://go.dev/dl/?mode=json&include=all' "$BUILD/downloads/go-index.json"
  python3 - "$BUILD/downloads" <<'PY'
import json,re,sys
from pathlib import Path
d=Path(sys.argv[1])
entries=[f for release in json.loads((d/'go-index.json').read_text()) for f in release['files'] if f['filename']=='go1.27.1.linux-amd64.tar.gz']
assert len(entries)==1 and re.fullmatch(r'[0-9a-f]{64}',entries[0]['sha256'])
(d/'go.sha256').write_text(entries[0]['sha256']+'  go.tar.gz\n')
PY
  if ! (cd "$BUILD/downloads" && sha256sum -c go.sha256); then
    fetch https://go.dev/dl/go1.27.1.linux-amd64.tar.gz "$BUILD/downloads/go.tar.gz"
    (cd "$BUILD/downloads" && sha256sum -c go.sha256)
  fi
  tar -xzf "$BUILD/downloads/go.tar.gz" -C "$BUILD/tools"
fi
if [ ! -x "$BUILD/tools/node/bin/node" ]; then
  base=https://nodejs.org/dist/v24.21.0
  fetch "$base/node-v24.21.0-linux-x64.tar.xz" "$BUILD/downloads/node-v24.21.0-linux-x64.tar.xz"
  fetch "$base/SHASUMS256.txt" "$BUILD/downloads/node-SHASUMS256.txt"
  (cd "$BUILD/downloads" && grep ' node-v24.21.0-linux-x64.tar.xz$' node-SHASUMS256.txt | sha256sum -c -)
  mkdir -p "$BUILD/tools/node"
  tar -xJf "$BUILD/downloads/node-v24.21.0-linux-x64.tar.xz" --strip-components=1 -C "$BUILD/tools/node"
fi
if [ ! -x "$BUILD/tools/pnpm/node_modules/.bin/pnpm" ]; then
  npm install --prefix "$BUILD/tools/pnpm" --ignore-scripts --no-audit --no-fund pnpm@9.15.9
fi
# Extract native compiler support into this build directory, never install or
# upgrade host system packages. apt-get download verifies repository hashes.
if ! ldconfig -p 2>/dev/null | grep -q 'libclang\.so'; then
  mkdir -p "$BUILD/tools/clang-debs" "$BUILD/tools/clang-root"
  (cd "$BUILD/tools/clang-debs" && apt-get download libclang1-14 libllvm14 libclang-common-14-dev)
  for f in "$BUILD/tools/clang-debs/"*.deb; do dpkg-deb -x "$f" "$BUILD/tools/clang-root"; done
  export LIBCLANG_PATH="$BUILD/tools/clang-root/usr/lib/llvm-14/lib"
  export LD_LIBRARY_PATH="$BUILD/tools/clang-root/usr/lib/x86_64-linux-gnu:${LD_LIBRARY_PATH:-}"
fi
cd "$ROOT"
{ rustc --version; cargo --version; node --version; pnpm --version; go version; } | tee "$BUILD/logs/toolchain.log"
cargo fmt --all --check > "$BUILD/logs/fmt.log" 2>&1
(cd console && pnpm install --frozen-lockfile && pnpm lint && pnpm test && pnpm build && pnpm i18n:check) > "$BUILD/logs/console.log" 2>&1
python3 -m unittest discover -s _ops/tests -v > "$BUILD/logs/python.log" 2>&1
cargo test --locked -p gproxy-core --lib no_idle_deadline_tests > "$BUILD/logs/idle-clock.log" 2>&1
cargo test --locked -p gproxy-core --test limits > "$BUILD/logs/limits.log" 2>&1
cargo test --locked -p gproxy-host-axum --test management > "$BUILD/logs/management.log" 2>&1
cargo test --locked --lib --tests > "$BUILD/logs/full-native.log" 2>&1
cargo build --locked --release --package gproxy --bin gproxy > "$BUILD/logs/release.log" 2>&1
cp "$CARGO_TARGET_DIR/release/gproxy" "$BUILD/artifact/gproxy"
"$BUILD/artifact/gproxy" --version | tee "$BUILD/artifact/version.txt"
"$BUILD/artifact/gproxy" --help >/dev/null
python3 - "$BUILD/artifact/build-info.json" <<'PY'
import json,os,sys
from pathlib import Path
Path(sys.argv[1]).write_text(json.dumps({'commit':os.environ['GPROXY_BUILD_HASH'],'version':os.environ['GPROXY_BUILD_VERSION'],'mode':'isolated-unprivileged-cpu','real_model_requests':0},indent=2))
PY
(cd "$BUILD" && tar -czf artifact/test-logs.tar.gz logs)
(cd "$BUILD/artifact" && sha256sum gproxy version.txt build-info.json test-logs.tar.gz > SHA256SUMS && tar -czf ../gproxy-cpu-build.tar.gz gproxy version.txt build-info.json test-logs.tar.gz SHA256SUMS)
sha256sum "$BUILD/gproxy-cpu-build.tar.gz" > "$BUILD/gproxy-cpu-build.tar.gz.sha256"
printf 'BUILD_PASSED\n'
