#!/bin/zsh
# Keeps both channels producing until every slot in schedule.buffer_days (15) is filled, then checks back every
# 30 min (owner 6 Nov: "don't stop the video creation until there are videos for 15 plus days").
# One loop per channel, side by side; each `daily` takes that channel's lock, so a launchd run that starts meanwhile
# just exits. Uploads stop at YouTube's shared quota (~5/day) and the rest wait, rendered, for the next day.
# Stop it: touch STOP (both channels) or STOP.<id>, or kill the loop.
cd "${0:A:h}/.."
mkdir -p logs

free_slots() {  # number of free slots in the buffer window for channel $1 ("" = cosmic)
  .venv/bin/python main.py ${=1} status 2>/dev/null | grep "^Free slots" | sed 's/.*days: //' |
    awk -F', ' '{ if ($0 == "none") print 0; else print NF }'
}

run_channel() {  # $1: "" or "--channel clips"; $2: log name
  while true; do
    if [[ -e STOP ]]; then sleep 600; continue; fi
    n=$(free_slots "$1")
    if [[ "${n:-1}" == "0" ]]; then
      echo "$(date '+%F %T') $2: buffer full, rechecking in 30 min" >> logs/fill-$2.log
      sleep 1800; continue
    fi
    echo "$(date '+%F %T') $2: $n free slots, starting a run" >> logs/fill-$2.log
    caffeinate -i -s .venv/bin/python -u main.py ${=1} daily --force >> logs/fill-$2.log 2>&1
    echo "$(date '+%F %T') $2: run exited $?" >> logs/fill-$2.log
    sleep 120  # lets a run that hit a provider's per-minute limit cool down before the next
  done
}

run_channel "" cosmic &
run_channel "--channel clips" clips &
wait
