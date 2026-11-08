#!/usr/bin/env bash
# One-time (re-runnable) setup of a free Ubuntu 24.04 VM (Oracle Cloud Always Free, Ampere A1 / arm64).
# Run as root on the VM after deploy/linux/push.sh has copied the repo to /opt/cosmos-pipeline:
#   sudo bash /opt/cosmos-pipeline/deploy/linux/setup-vm.sh
# It installs packages, builds the venv, checks FFmpeg, runs the tests and installs (but does NOT enable) the
# systemd timers. Scheduling only starts with deploy/linux/cutover.sh, so the Mac and the VM never both run.
set -euo pipefail
APP=/opt/cosmos-pipeline
cd "$APP"

echo "== packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y sudo python3.12 python3.12-venv python3-pip ffmpeg tzdata git rsync ca-certificates

echo "== timezone"
ln -sf /usr/share/zoneinfo/Asia/Kolkata /etc/localtime && echo "Asia/Kolkata" > /etc/timezone || true

echo "== service user"
id pipeline >/dev/null 2>&1 || useradd --system --home "$APP" --shell /usr/sbin/nologin pipeline
mkdir -p "$APP/logs" "$APP/output"
# Safety: the VM starts paused. cutover.sh removes these once the Mac's schedulers are off.
[ -f "$APP/.cutover" ] || touch "$APP/STOP.cosmic" "$APP/STOP.clips"
chown -R pipeline:pipeline "$APP"
for f in .env youtube_token.json youtube_analytics_token.json client_secret.json; do
  [ -f "$APP/$f" ] && chmod 600 "$APP/$f"
done
[ -d "$APP/credentials" ] && find "$APP/credentials" -type f -exec chmod 600 {} +

echo "== python venv"
sudo -u pipeline python3.12 -m venv "$APP/.venv"
sudo -u pipeline "$APP/.venv/bin/pip" install --quiet --upgrade pip
sudo -u pipeline "$APP/.venv/bin/pip" install --quiet -r "$APP/requirements.lock.txt"
[ -f "$APP/requirements-dev.txt" ] && sudo -u pipeline "$APP/.venv/bin/pip" install --quiet -r "$APP/requirements-dev.txt" || true

echo "== ffmpeg filters (expect 6)"
n=$(ffmpeg -hide_banner -filters 2>/dev/null | grep -cE " (subtitles|zoompan|sidechaincompress|loudnorm|ebur128|signalstats) " || true)
echo "found $n"; [ "$n" -ge 6 ] || { echo "FFmpeg is missing filters"; exit 1; }

echo "== tests"
sudo -u pipeline env HOME="$APP" "$APP/.venv/bin/python" -m pytest -q -x || { echo "TESTS FAILED"; exit 1; }

echo "== pipeline sanity"
sudo -u pipeline env HOME="$APP" "$APP/.venv/bin/python" main.py channels || true

echo "== systemd units (installed, not enabled)"
if [ -d /run/systemd/system ]; then
  cp deploy/linux/cosmos-pipeline@.service deploy/linux/cosmos-pipeline-*.timer /etc/systemd/system/
  systemctl daemon-reload
  echo "Timers installed. Enable them with deploy/linux/cutover.sh."
else
  echo "systemd not running here (container test): skipped unit install"
fi
echo "SETUP OK"
