#!/usr/bin/env bash
# bankai installer - one-line install on Linux/macOS.
#   curl -sSfL https://raw.githubusercontent.com/73gon/bankai/main/scripts/install.sh | bash
set -euo pipefail

PREFIX="${BANKAI_PREFIX:-$HOME/.local/share/bankai}"
BIN_DIR="${BANKAI_BIN:-$HOME/.local/bin}"
REPO="${BANKAI_REPO:-https://github.com/73gon/bankai.git}"
REF="${BANKAI_REF:-main}"

say() { printf '\033[1;35m[bankai]\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31m[bankai]\033[0m %s\n' "$*" >&2; exit 1; }

command -v python3 >/dev/null || fail "python3 not found"
command -v git >/dev/null || fail "git not found"

# Detect missing system binaries (don't auto-install — user knows their distro).
missing=()
for b in ffmpeg ffprobe mkvmerge alass; do
  command -v "$b" >/dev/null || missing+=("$b")
done
if (( ${#missing[@]} )); then
  say "WARNING: missing binaries: ${missing[*]}"
  say "On Debian/Ubuntu: sudo apt install ffmpeg mkvtoolnix"
  say "alass:           cargo install alass-cli   (or download from github.com/kaegi/alass)"
fi

mkdir -p "$BIN_DIR"

if [[ -e "$PREFIX" && ! -d "$PREFIX/.git" ]]; then
  if [[ "${BANKAI_FORCE_REINSTALL:-0}" == "1" ]]; then
    say "removing non-git install at $PREFIX"
    rm -rf "$PREFIX"
  else
    fail "$PREFIX exists but is not a git checkout. Re-run with BANKAI_FORCE_REINSTALL=1 to replace it."
  fi
fi

if [[ -d "$PREFIX/.git" ]]; then
  say "updating existing checkout in $PREFIX"
  git -C "$PREFIX" fetch --depth 1 origin "$REF"
  git -C "$PREFIX" reset --hard "FETCH_HEAD"
else
  say "cloning bankai into $PREFIX"
  mkdir -p "$(dirname "$PREFIX")"
  git clone --depth 1 --branch "$REF" "$REPO" "$PREFIX"
fi

if [[ ! -d "$PREFIX/.venv" ]]; then
  say "creating venv"
  python3 -m venv "$PREFIX/.venv"
fi

say "installing bankai"
"$PREFIX/.venv/bin/pip" install --quiet --upgrade pip
"$PREFIX/.venv/bin/pip" install --quiet -e "$PREFIX"

say "installing playwright chromium"
"$PREFIX/.venv/bin/playwright" install chromium >/dev/null

ln -sf "$PREFIX/.venv/bin/bankai" "$BIN_DIR/bankai"

say "installed: $($PREFIX/.venv/bin/bankai --version)"
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) say "Add to PATH: echo 'export PATH=\"$BIN_DIR:\$PATH\"' >> ~/.bashrc && source ~/.bashrc" ;;
esac
say "next: bankai config init  &&  bankai doctor"
