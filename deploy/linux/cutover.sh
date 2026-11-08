#!/usr/bin/env bash
# Run on the Mac: move scheduling of BOTH channels from this Mac to the VM in one step.
#   deploy/linux/cutover.sh <vm-ip>
# 1. stops the Mac's launchd agents and pauses the Mac (STOP files), 2. copies the latest data/ to the VM,
# 3. unpauses the VM and enables its timers. Two schedulers at once would double-upload, so this is all-or-nothing.
set -euo pipefail
HOST=${1:?usage: cutover.sh <vm-ip> [ssh-user]}
USER_=${2:-ubuntu}
KEY=${SSH_KEY:-$HOME/.ssh/cosmos_vm}
SSH="ssh -i $KEY"
cd "$(dirname "$0")/../.."

echo "== Mac: stop schedulers"
for a in com.cosmicscale.daily com.cosmicscale.clips; do launchctl bootout "gui/$(id -u)/$a" 2>/dev/null || true; done
touch STOP.cosmic STOP.clips
while pgrep -f "main.py.*daily" >/dev/null; do echo "waiting for a running Mac job to finish..."; sleep 30; done

echo "== copy latest state"
$SSH "$USER_@$HOST" "sudo chown -R $USER_ /opt/cosmos-pipeline/data"
rsync -az -e "$SSH" --exclude '*.bak' data/ "$USER_@$HOST:/opt/cosmos-pipeline/data/"
# jobs that are made but not yet uploaded need their folders
recent=$(mktemp)
# only jobs made but not uploaded yet (scheduled/published ones already live on YouTube)
python3 - > "$recent" <<'PY'
import json, os
done = {"published", "scheduled", "failed", "quarantined", "withdrawn", "deleted", "rejected"}
for hist, base in (("data/history.json", ""), ("data/channels/clips/history.json", "channels/clips/")):
    for j in json.load(open(hist)):
        if j.get("status") not in done and j.get("folder") and os.path.isdir(f"output/{base}{j['folder']}"):
            print(base + j["folder"])
PY
echo "copying $(wc -l < "$recent") unuploaded job folders"
rsync -az -e "$SSH" --files-from="$recent" -r output/ "$USER_@$HOST:/opt/cosmos-pipeline/output/" || true

echo "== VM: unpause and enable timers"
$SSH "$USER_@$HOST" "sudo chown -R pipeline:pipeline /opt/cosmos-pipeline && \
  sudo rm -f /opt/cosmos-pipeline/STOP.cosmic /opt/cosmos-pipeline/STOP.clips && sudo touch /opt/cosmos-pipeline/.cutover && \
  sudo systemctl enable --now cosmos-pipeline-cosmic.timer cosmos-pipeline-clips.timer && \
  systemctl list-timers 'cosmos-pipeline-*' --no-pager"
echo "CUTOVER DONE: the VM now runs both channels. The Mac stays paused (STOP files)."
