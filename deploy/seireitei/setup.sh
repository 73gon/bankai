#!/usr/bin/env bash
# Prepare bankai to run under WSL on keller. Run once, with sudo:
#
#     sudo bash /home/malik/projects/bankai/deploy/seireitei/setup.sh
#
# It prepares only. Nothing is started, because the Windows service still owns
# the release state and qBittorrent, and two instances sharing those would
# corrupt both. The cutover commands are printed at the end.
#
# Safe to re-run: every step checks before acting.
set -euo pipefail

REPO=/home/malik/projects/bankai
VENV="$REPO/.venv"
OWNER=malik
UNIT=/etc/systemd/system/bankai-web.service

if [[ $EUID -ne 0 ]]; then
  echo "Run with sudo: sudo bash $0" >&2
  exit 1
fi

echo "==> System packages"
# python3.12-venv: Ubuntu ships venv without ensurepip, so a plain
#   'python3 -m venv' fails halfway and leaves an unusable directory.
# ffmpeg: provides ffprobe, which the codec sweep and the German-dub check
#   both depend on. Without it every episode reads as an unknown encode.
# mkvtoolnix: mkvmerge, for remuxing.
NEEDED=()
for pkg in python3.12-venv ffmpeg mkvtoolnix; do
  dpkg -s "$pkg" >/dev/null 2>&1 || NEEDED+=("$pkg")
done
if ((${#NEEDED[@]})); then
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${NEEDED[@]}"
  echo "    installed: ${NEEDED[*]}"
else
  echo "    already present"
fi

echo "==> Python environment"
if [[ ! -x "$VENV/bin/bankai" ]]; then
  sudo -u "$OWNER" python3 -m venv "$VENV"
  sudo -u "$OWNER" "$VENV/bin/pip" install --quiet --upgrade pip
  sudo -u "$OWNER" "$VENV/bin/pip" install --quiet -e "$REPO[web]"
  echo "    built $VENV"
else
  sudo -u "$OWNER" "$VENV/bin/pip" install --quiet -e "$REPO[web]"
  echo "    refreshed $VENV"
fi
sudo -u "$OWNER" "$VENV/bin/bankai" --help >/dev/null && echo "    bankai runs"

echo "==> Directories"
# Downloads live on G:, not inside the WSL image. The image grows on C:
# with whatever is written into it and never shrinks back, and the free space
# it reports is its own virtual maximum rather than what C: has left -- so the
# 100 GiB reserve never fired, and 233 GB of torrents filled C: to zero. On G:
# the reported free space is real, and the download already sits on the same
# volume as staging and the library.
sudo -u "$OWNER" mkdir -p /mnt/g/qbit/.incomplete /home/malik/bankai-work
# Staging shares a volume with the library so publishing is a rename rather
# than a second pass over 9p, and sits outside /mnt/g/media so a half-written
# file is never inside a scanned root.
mkdir -p /mnt/g/bankai/staging

echo "==> systemd unit"
install -m 644 "$REPO/deploy/seireitei/bankai-web.service" "$UNIT"
systemctl daemon-reload
echo "    installed $UNIT (not enabled, not started)"

echo "==> tailscale serve"
# bankai takes 7003 and qBittorrent 7004, after 7001 and 7002.
if tailscale serve status 2>/dev/null | grep -q ':7003'; then
  echo "    7003 already served"
else
  tailscale serve --bg --http=7003 http://127.0.0.1:3003
  echo "    7003 -> 127.0.0.1:3003"
fi

if ! tailscale serve status 2>/dev/null | grep -q ':7004'; then
  tailscale serve --bg --http=7004 http://127.0.0.1:8080
  echo "    7004 -> 127.0.0.1:8080 (qBittorrent)"
fi

cat <<'DONE'

==> Ready. Nothing is running yet.

Before starting, the Windows service must stop owning the data:

  1. On Windows:   Stop-Service bankai-web ; Set-Service bankai-web -StartupType Disabled
  2. Copy the release state across. It holds 2000+ releases, every blacklist
     decision and every TVDB mapping, and is not reproducible:
       /mnt/c/Windows/System32/config/systemprofile/AppData/Local/bankai/erai_automation.json
     to
       /home/malik/.local/state/bankai/erai_automation.json
  3. Point the config at Linux paths (/mnt/g/media/..., /mnt/g/qbit).

Then:

  sudo systemctl enable --now bankai-web
  systemctl status bankai-web
  journalctl -u bankai-web -f

DONE
