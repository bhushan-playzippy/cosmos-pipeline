#!/usr/bin/env bash
# Run on the Mac: copy the code, state and secrets to the VM, then run setup-vm.sh there.
#   deploy/linux/push.sh <vm-ip>            (ssh user defaults to ubuntu, key ~/.ssh/cosmos_vm)
# Secrets travel over SSH only, never through git. Safe to re-run.
set -euo pipefail
HOST=${1:?usage: push.sh <vm-ip> [ssh-user]}
USER_=${2:-ubuntu}
KEY=${SSH_KEY:-$HOME/.ssh/cosmos_vm}
SSH="ssh -i $KEY -o StrictHostKeyChecking=accept-new"
cd "$(dirname "$0")/../.."

$SSH "$USER_@$HOST" "sudo mkdir -p /opt/cosmos-pipeline && sudo chown -R $USER_ /opt/cosmos-pipeline"
# After cutover the VM owns the live state (data/): never overwrite it from the Mac again.
DATA_RULE=""
$SSH "$USER_@$HOST" "test -f /opt/cosmos-pipeline/.cutover" && DATA_RULE="--exclude data/" && echo "(VM is live: data/ not copied)"

echo "== code + state"
rsync -az --delete -e "$SSH" $DATA_RULE --exclude .cutover \
  --exclude .venv/ --exclude output/ --exclude logs/ --exclude __pycache__/ --exclude .pytest_cache/ \
  --include 'archive/' --include 'archive/*.yaml' --exclude 'archive/*' --exclude tmp/ --exclude 'STOP*' --exclude .pipeline.lock \
  --exclude docstudio/footage/ --exclude docstudio/work/ --exclude docstudio/out/ \
  --exclude docstudio/ig/ --exclude docstudio/ig2/ \
  --exclude .env --exclude youtube_token.json --exclude youtube_analytics_token.json \
  --exclude client_secret.json --exclude credentials/ \
  ./ "$USER_@$HOST:/opt/cosmos-pipeline/"

echo "== secrets (over ssh)"
for f in .env youtube_token.json youtube_analytics_token.json client_secret.json; do
  [ -f "$f" ] && rsync -az -e "$SSH" --chmod=F600 "$f" "$USER_@$HOST:/opt/cosmos-pipeline/$f"
done
[ -d credentials ] && rsync -az -e "$SSH" --chmod=D700,F600 credentials/ "$USER_@$HOST:/opt/cosmos-pipeline/credentials/"

echo "== setup on the VM"
$SSH "$USER_@$HOST" "sudo bash /opt/cosmos-pipeline/deploy/linux/setup-vm.sh"
