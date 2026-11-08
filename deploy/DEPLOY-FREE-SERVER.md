# Run the pipeline 24/7 on a free server

**Choice: Oracle Cloud "Always Free"**, an Ampere A1 Linux VM with up to 4 CPU cores, 24 GB RAM and 200 GB disk, free
with no time limit. It's the only free option big enough to render videos every day, and it uses the same chip type
(ARM) as this Mac. The setup scripts were tested on Ubuntu 24.04 ARM: all 467 tests pass, FFmpeg has every filter the
pipeline needs, and both channels are detected.

What moves to the server: the daily YouTube pipeline for **both channels** (make → fact-check → QA → upload →
schedule → comments → analytics), on the same timetable as now. Phone alerts (ntfy) keep working.
What stays manual: Instagram scheduling (Business Suite needs a browser login) and the 4K documentaries (docstudio,
heavy and hand-checked).

## Part 1: what YOU do (about 15 minutes, once)

Claude can't create accounts or enter card details, so this part is yours.

1. **Sign up:** https://signup.cloud.oracle.com
   - Pick **Home Region = India West (Mumbai)** or **India South (Hyderabad)**. It can't be changed later.
   - Oracle asks for a debit/credit card **only to verify you**. A small temporary hold is refunded.
     "Always Free" resources are never charged.
2. **Create the server:** Menu → Compute → Instances → **Create instance**
   - Name: `cosmos-pipeline`
   - Image: **Canonical Ubuntu 24.04** (pick the aarch64 build)
   - Shape: **Ampere → VM.Standard.A1.Flex**, **4 OCPUs, 24 GB memory**. It must show the "Always Free-eligible" tag.
   - Networking: leave the defaults, keeping "Assign a public IPv4 address" on.
   - **Add SSH keys → Paste public keys**, and paste exactly this one line:
     ```
     ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIP/5h+gpcqNZsTy/njH7cntrEHwGToiEYyUqKgDGCSbh cosmos-vm
     ```
   - Boot volume: set **100 GB** (free up to 200 GB).
   - Click **Create**. If you see **"Out of capacity"**, try another Availability Domain or try again a few hours later.
     This is common for free ARM servers and is not your fault.
3. **Send Claude the server's Public IP address** (shown on the instance page, for example `140.238.x.x`).
4. Recommended: Billing → **Budgets** → create a budget of **$1** with an email alert, so any charge is flagged.

Note: Oracle may reclaim Always Free servers that sit idle for 7 days. This pipeline renders every day, but if it
ever happens, upgrading the account to "Pay As You Go" stops reclamation. You still pay ₹0 as long as you only use
Always Free shapes, and the $1 budget alert protects you.

## Part 2: what CLAUDE does once you send the IP

```bash
deploy/linux/push.sh <ip>      # copies code + state, sends secrets over SSH, installs everything, runs all tests
                               # the server starts PAUSED (STOP files), so nothing runs yet
# dry run of each channel on the server, then:
deploy/linux/cutover.sh <ip>   # turns OFF the Mac's schedulers, copies the latest state, turns ON the server's timers
```

Only one machine ever runs a channel. cutover.sh switches both channels over in a single step, so nothing gets
uploaded twice. After that, the Mac can be off.

## Daily checks (optional)

```bash
ssh -i ~/.ssh/cosmos_vm ubuntu@<ip> 'systemctl list-timers "cosmos-pipeline-*"; tail -20 /opt/cosmos-pipeline/logs/cosmic.log'
ssh -i ~/.ssh/cosmos_vm ubuntu@<ip> 'cd /opt/cosmos-pipeline && sudo -u pipeline .venv/bin/python main.py status'
```

- **Emergency stop:** `ssh -i ~/.ssh/cosmos_vm ubuntu@<ip> 'sudo touch /opt/cosmos-pipeline/STOP'`
- **Code updates:** re-run `push.sh <ip>`. After cutover it never overwrites the server's live `data/`.

## Why not the other free options

| Option | Problem |
|---|---|
| GitHub Actions | Private repos get 2,000 min/month. Two channels need about 2,400. State would live in git commits. Works only as a backup plan. |
| Google Cloud free e2-micro | 1 GB RAM: too small to render video. |
| Render / Railway / Fly free tiers | Sleep or time out, have little RAM, and have no cron long enough for rendering. |
