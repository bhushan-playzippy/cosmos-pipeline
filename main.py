"""Cosmic Scale: faceless Shorts pipeline. Command line over pipeline/orchestrator.py.

Daily operation
  python main.py daily                  # what the scheduler runs: sync → uploads → make videos for free slots
                                        #   → uploads → comments → analytics + dashboard (reports/daily-*.md)
  python main.py status                 # queue, slots, alerts, readiness at a glance
  python main.py approve output/<folder> | --all     # approve videos (uploads into their slot)
  python main.py reject output/<folder> --note "..."  # turn one down; the slot gets a replacement
  python main.py withdraw output/<folder> --note "..." # pull a SCHEDULED upload: kept private, no publish time
  python main.py reschedule output/<folder> | --over-cap   # move a scheduled private upload to the next free slot;
                                        #   --over-cap moves every one beyond schedule.videos_per_day on its day
  touch STOP                            # kill switch: no new work or publishing until you delete STOP
  python main.py daily --dry-run        # everything except uploads, deletes and comments

Production
  python main.py ideas                  # brainstorm + score topics into data/topics.json
  python main.py calendar [--fill]      # 30-day plan → reports/[<id>/]CONTENT_CALENDAR.md (--fill brainstorms enough topics)
  python main.py make [--topic "..."] [--count N]   # make videos (gated), not uploaded
  python main.py rerender output/<folder>           # re-render an edited script.json
  python main.py add-music output/<folder> | --all [--replace]
                                        # mix background music under videos made without it (same picture);
                                        #   --replace also swaps already-scheduled private uploads on YouTube
  python main.py veo-prompts            # Veo hook-clip prompts for the next topics

Publishing
  python main.py publish output/<folder> [--force]  # upload one ready video into its slot
  python main.py crosspost              # post Shorts that went public on YouTube as Instagram Reels (if enabled)
  python main.py upload-pending         # upload every ready video (bounded by limits/quota)
  python main.py sync                   # confirm scheduled → published with YouTube

Measurement and money
  python main.py report | weekly        # dashboards in reports/
  python main.py quality [--count DAYS] # content gates, failure classes, claim/visual rates, model usage (default 7)
  python main.py inspect output/<folder>  # one video's claim ledger, citations, visual grounding, verification state
  python main.py comments               # read viewer comments now + what the comments agent learned from them
  python main.py money                  # owner finance (private, terminal only): actuals, net cash, 30-day forecast
  python main.py money balance --amount 8000 --ref "bank app 2 Nov" --confirm      # personal cash on hand
  python main.py money add --book personal|business --kind TYPE [--category C] [--account A] --amount N
                        --ref REF --confirm [--date YYYY-MM-DD] [--note "..."]   # one actual entry (idempotent)
  python main.py money withdraw --amount N --ref REF --confirm   # business → personal, recorded on both books
  python main.py money plan --direction inflow|outflow --note LABEL --amount MIN [--max MAX] --date D [--until D]
                        [--category C] [--spread] [--assumption "..."] [--action]   # forecast item, never cash
  python main.py money plan-done|plan-cancel --note <id>  ·  money forecast  ·  money reconcile
  python main.py money plan-set --note <id> --reason "why" [--amount N] [--max N] [--date D] [--until D]
                        [--optional|--required] [--unconfirmed|--dates-confirmed]   # correct a forecast item
  python main.py money reverse --note <event id> --ref REF --confirm   # undo a mistaken entry (history kept)
  Debt (phase C; nothing here pays, forecloses or contacts anyone):
  python main.py money debts                     # accounts, instalment status, reconciliation issues, total
  python main.py money debt-add|debt-set --account ID [--lender L] [--loan-type T] [--account-ref NUMBER(masked)]
        [--principal N --as-of D] [--rate PCT --rate-type reducing|flat|unknown] [--emi N] [--due-day N]
        [--next-due D] [--overdue N] [--fees N] [--foreclosure N --valid-until D --foreclosure-charges N
        --lender-confirmed] [--in-aggregate] [--debt-kind loan|aggregate|personal] [--note "..."]
  python main.py money debt-statement --account ID --date D --principal N --ref "statement source"
        [--payments "2026-10-05:15000,2026-11-05:15000"] [--interest N] [--fees N]
  python main.py money strategy                  # minimum / avalanche / snowball (impact only with verified data)
  (the old per-channel `finance` ledger is retired: it summed currencies, used floats and had no ids)

Setup
  python main.py auth-youtube | auth-analytics | channel-setup | refresh-instagram

Channels (docs/MULTI_CHANNEL.md)
  python main.py channels                         # list profiles and check they share no identity/credentials
  python main.py --channel clips <command>        # any command above, on another channel (default: config.yaml's)
  Clip channel only:
  python main.py --channel clips discover         # find sources (NASA library, inbox, optional YouTube leads)
  python main.py --channel clips sources          # registry with rights status; the manual review queue
  python main.py --channel clips rights-approve '<source id>' --reviewer NAME [--note "..."]
  python main.py --channel clips rights-reject '<source id>' --reviewer NAME --note "why"
  python main.py --channel clips revoke '<source id>' --note "permission withdrawn ..."
  python main.py --channel clips add-source path/to/video.mp4 --rights rights.yaml [--srt captions.srt]
"""
import argparse
import contextlib
import sys
from pathlib import Path

from pipeline import channels, control
from pipeline.util import load_env, slugify

COMMANDS = ["daily", "status", "approve", "reject", "ideas", "calendar", "make", "rerender", "veo-prompts", "publish",
            "upload-pending",
            "sync", "report", "weekly", "finance", "auth-youtube", "auth-analytics", "channel-setup",
            "refresh-instagram", "auto", "channels", "discover", "sources", "rights-approve", "rights-reject",
            "revoke", "add-source", "comments", "crosspost", "add-music", "withdraw", "reschedule", "quality", "inspect", "money"]
# commands that change the queue/registry take the channel's lock (a second concurrent run exits)
MUTATING = {"daily", "auto", "approve", "reject", "make", "rerender", "publish", "upload-pending", "sync", "discover",
            "rights-approve", "rights-reject", "revoke", "add-source", "comments", "crosspost", "add-music",
            "withdraw", "reschedule"}
GENERATED_ONLY = {"ideas", "calendar", "rerender", "veo-prompts", "refresh-instagram", "add-music"}
CLIPS_ONLY = {"discover", "sources", "rights-approve", "rights-reject", "revoke", "add-source"}


def status(cfg):
    from pipeline import jobs, analytics
    all_jobs = jobs.load()
    print(f"\nKill switch: {'ON' if control.STOP_FILE.exists() or cfg.get('control', {}).get('kill_switch') else 'off'}")
    print("\nQueue:")
    for j in sorted(all_jobs, key=lambda j: (jobs.when(j) or jobs.now()).isoformat()):
        if j["status"] == "deleted":
            continue
        w = jobs.when(j)
        print(f"  {j['status']:<12} {w.strftime('%a %d %b %H:%M') if w else '-':<16} {j.get('role') or '-':<10} "
              f"{j['topic'][:44]:<44} {j.get('youtube_id') or ''} {('⚠ ' + j['alert']) if j.get('alert') else ''}")
    free = jobs.free_slots(cfg, all_jobs, cfg["schedule"].get("buffer_days", 2))
    print(f"\nFree slots in the next {cfg['schedule'].get('buffer_days', 2)} days: "
          + (", ".join(f"{s:%a %H:%M}" for s in free) or "none"))
    from pipeline import orchestrator as orch
    waiting = orch.awaiting(all_jobs)
    if waiting:
        print(f"\n{len(waiting)} video(s) AWAITING YOUR APPROVAL — watch video.mp4 and read post.txt, then:")
        for j in waiting:
            w = jobs.when(j)  # a rerendered video awaiting approval may have no slot yet (approve picks one)
            print(f"  {w.strftime('%a %d %b %H:%M') if w else 'next free slot':<16}  "
                  f"python main.py approve output/{j['folder']}")
        print('  (or: python main.py approve --all    |   python main.py reject output/<folder> --note "why")')
    r = analytics.readiness(cfg)
    print(f"\nSubscribers (last snapshot): {r['subscribers']} / {r['thresholds'].get('subscribers', '?')}")


def sources_table(cfg):
    from pipeline.clips import registry
    srcs = sorted(registry.sources(cfg).values(), key=lambda s: (s["status"], -s.get("score", 0)))
    for s in srcs:
        rv = s.get("review") or {}
        print(f"  {s['status']:<14} {s.get('score', 0):<5} {s['id'][:60]:<60} {s.get('title', '')[:40]}")
        for reason in rv.get("hard", []):
            print(f"      ✗ {reason}")
        for reason in rv.get("soft", []) if s["status"] == "manual_review" else []:
            print(f"      ? {reason}")
    print(f"\n{len(srcs)} source(s). Approve after checking: python main.py --channel {cfg['channel']['id']} "
          f"rights-approve '<id>' --reviewer <you>")


def main():
    load_env()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=COMMANDS)
    ap.add_argument("--channel", help="channel profile id (default: config.yaml's channel; see `channels`)")
    ap.add_argument("--reviewer", help="rights decisions: who decided")
    ap.add_argument("--rights", help="add-source: YAML file with the source's rights record")
    ap.add_argument("--srt", help="add-source: captions file (.srt/.vtt)")
    ap.add_argument("--title")
    ap.add_argument("--creator")
    ap.add_argument("folder", nargs="?")
    ap.add_argument("--topic")
    ap.add_argument("--count", type=int, default=1)
    ap.add_argument("--force", action="store_true", help="publish even if the job didn't pass the gates")
    ap.add_argument("--dry-run", action="store_true", help="no uploads, deletes or comments")
    ap.add_argument("--fill", action="store_true", help="calendar: brainstorm topics until 30 days are covered")
    ap.add_argument("--all", action="store_true", help="approve: every video awaiting approval; add-music: every "
                                                       "queued video without music")
    ap.add_argument("--over-cap", action="store_true", help="reschedule: every scheduled upload beyond the daily target")
    ap.add_argument("--replace", action="store_true", help="add-music: also replace scheduled (still private) uploads")
    for f in ("kind", "category", "amount", "status", "note", "currency", "book", "account", "ref", "date", "max",
              "until", "direction", "assumption"):
        ap.add_argument(f"--{f}")
    ap.add_argument("--confirm", action="store_true", help="money: you confirm this entry happened (required)")
    ap.add_argument("--spread", action="store_true", help="money plan: spread the amount over the date range")
    ap.add_argument("--action", action="store_true", help="money plan: flag the item as needing your action")
    for f in ("lender", "loan-type", "account-ref", "principal", "as-of", "rate", "rate-type", "emi", "due-day",
              "next-due", "overdue", "fees", "foreclosure", "valid-until", "foreclosure-charges", "debt-kind",
              "payments", "interest", "reason"):
        ap.add_argument(f"--{f}")
    for f in ("lender-confirmed", "in-aggregate", "optional", "required", "unconfirmed", "dates-confirmed"):
        ap.add_argument(f"--{f}", action="store_true")
    a = ap.parse_args()
    control.DRY_RUN = a.dry_run
    if a.cmd == "money":  # owner-level: no channel, no channel lock (the business store has its own lock)
        return money(a)
    if a.cmd == "channels":
        channels.validate_all()
        for cid in channels.available():
            c = channels.load(cid)
            print(f"  {cid:<10} {c['channel'].get('type', 'generated'):<10} {c['channel']['name']:<32} "
                  f"yt={c['channel'].get('youtube_channel_id') or '(not linked)':<26} "
                  f"{c['schedule']['videos_per_day']}/day uploads={'on' if c['youtube'].get('enabled') else 'off'}")
        print("OK: no two channels share a YouTube channel id, token file, token variable or data folder.")
        return
    cfg = channels.activate(channels.load(a.channel))
    kind = cfg["channel"].get("type", "generated")
    if kind == "clips" and a.cmd in GENERATED_ONLY or kind != "clips" and a.cmd in CLIPS_ONLY:
        sys.exit(f"'{a.cmd}' isn't available for {kind} channel '{cfg['channel']['id']}'")

    from pipeline import orchestrator as orch
    from pipeline.clips import orchestrator as clips
    if a.cmd in ("daily", "auto"):
        # Run watchdog: a stalled network read once held the channel lock for 13 h and blocked every later run.
        # On expiry it dumps every thread's stack to the log and exits; the OS releases the flock, writes are
        # atomic, and an interrupted upload is reconciled by the next run.
        watchdog(cfg.get("control", {}).get("max_run_minutes", 180) * 60)
    with channels.lock(cfg) if a.cmd in MUTATING else contextlib.nullcontext():
        try:
            run(a, cfg, kind, orch, clips)
        finally:
            control.write_usage(a.cmd)


def money(a):
    """Owner finance commands (pipeline/business). Output goes to the terminal only, never to reports/."""
    from pipeline.business import money as m, store
    sub = a.folder or "summary"
    try:
        if sub == "summary":
            print("\n".join(m.summary_lines()))
        elif sub == "forecast":
            print("\n".join(m.forecast_lines(m.forecast(days=a.count if a.count > 1 else 30))))
        elif sub == "balance":
            ev, new = m.add("personal", "balance", a.amount, a.ref, a.confirm, date=a.date, note=a.note or "")
            print(f"{'recorded' if new else 'already recorded'}: {ev['id']} balance {store.rupees(ev['amount_paise'])}")
        elif sub == "add":
            ev, new = m.add(a.book, a.kind, a.amount, a.ref, a.confirm, category=a.category, account=a.account,
                            date=a.date, note=a.note or "", currency=(a.currency or "INR").upper())
            print(f"{'recorded' if new else 'already recorded (no change)'}: {ev['id']} {ev['book']} {ev['type']} "
                  f"{store.rupees(ev['amount_paise'], ev['currency'])}")
        elif sub == "withdraw":
            (x, _), (y, _) = m.withdraw(a.amount, a.ref, a.confirm, date=a.date, note=a.note or "")
            print(f"recorded: {x['id']} (business) + {y['id']} (personal) {store.rupees(x['amount_paise'])}")
        elif sub == "plan":
            item, new = m.plan(a.note, a.direction, a.amount, a.date, a.max, a.until, a.category or "other",
                               a.spread, a.assumption or "", a.action, optional=a.optional,
                               confirmed=not a.unconfirmed)
            print(f"{'planned' if new else 'already planned'}: {item['id']} {item['label']}")
        elif sub == "plan-set":
            fields = {"min_paise": store.paise(a.amount) if a.amount else None,
                      "max_paise": store.paise(a.max or a.amount) if (a.max or a.amount) else None,
                      "earliest": a.date, "latest": a.until or (a.date if a.date else None),
                      "optional": True if a.optional else False if a.required else None,
                      "confirmed": False if a.unconfirmed else True if a.dates_confirmed else None,
                      "assumption": a.assumption, "label": None, "needs_action": True if a.action else None}
            print(m.plan_set(a.note, a.reason, **fields))
        elif sub == "reverse":
            ev, new = m.reverse(a.note, a.ref, a.confirm, date=a.date, note=a.reason or "")
            print(f"{'reversed' if new else 'already reversed'}: {a.note} by {ev['id']}")
        elif sub in ("debt-add", "debt-set"):
            from pipeline.business import debt
            if sub == "debt-add" and debt.get(a.account):
                sys.exit(f"debt account {a.account} exists: use debt-set")
            acc, changed = debt.upsert(a.account, lender=a.lender, loan_type=a.loan_type, account_ref=a.account_ref,
                                       principal_paise=a.principal, principal_as_of=a.as_of, rate_annual_pct=a.rate,
                                       rate_type=a.rate_type, emi_paise=a.emi, due_day=a.due_day, next_due=a.next_due,
                                       overdue_paise=a.overdue, fees_paise=a.fees, foreclosure_quote_paise=a.foreclosure,
                                       foreclosure_valid_until=a.valid_until,
                                       foreclosure_charges_paise=a.foreclosure_charges,
                                       foreclosure_confirmed_by_lender=True if a.lender_confirmed else None,
                                       in_aggregate=True if a.in_aggregate else None, kind=a.debt_kind, note=a.note)
            print(f"{'saved' if changed else 'no change'}: {acc['id']} ({acc['lender']})")
        elif sub == "debt-statement":
            from pipeline.business import debt
            pays = [tuple(x.split(":", 1)) for x in (a.payments or "").split(",") if x.strip()]
            acc, new = debt.add_statement(a.account, a.date, a.principal, pays, a.interest, a.fees, a.ref)
            print(f"{'statement added' if new else 'statement already entered'}: {acc['id']} now {acc['statement_status']}")
            print("\n".join(f"  REVIEW: {i}" for i in debt.reconcile(a.account)) or "  matches the ledger")
        elif sub == "debts":
            from pipeline.business import debt
            print("\n".join(debt.lines()))
        elif sub == "strategy":
            from pipeline.business import debt
            fc = m.forecast()
            rec = m.recommendation(m.business(), fc)
            print("\n".join(debt.strategy_lines(rec.get("extra_debt", 0), not fc.get("warning", True))))
        elif sub in ("plan-done", "plan-cancel"):
            if not a.note:
                sys.exit(f"usage: python main.py money {sub} --note <item id>")
            print(m.plan_status(a.note, "done" if sub == "plan-done" else "cancelled"))
        elif sub == "reconcile":
            evs, problems = store.reconcile(m.validate)
            print(f"{len(evs)} valid entr(y/ies), {len(problems)} problem(s)")
            for p in problems:
                print(f"  #{p['index']} {p['id'] or ''}: {p['problem']}")
        else:
            sys.exit(f"unknown money command {sub!r} (summary, forecast, balance, add, withdraw, reverse, plan, plan-set, "
                     "plan-done, plan-cancel, reconcile, debts, debt-add, debt-set, debt-statement, strategy)")
    except ValueError as e:
        sys.exit(f"not recorded: {e}")


def watchdog(seconds):
    """Wall-clock run limit. faulthandler's own timer uses a clock that stops while the Mac sleeps (a run passed
    7 h with the 3 h limit unfired), so this thread checks time.time() and, on expiry, dumps every thread's stack to
    the log and exits. The OS releases the flock, writes are atomic, and an interrupted upload is reconciled next run."""
    import faulthandler
    import os
    import threading
    import time
    deadline = time.time() + seconds

    def watch():
        while time.time() < deadline:
            time.sleep(30)
        print(f"watchdog: run exceeded {seconds // 60} min (wall clock); stopping", flush=True)
        faulthandler.dump_traceback(all_threads=True)
        os._exit(3)
    threading.Thread(target=watch, daemon=True, name="watchdog").start()


def run(a, cfg, kind, orch, clips):
    if a.cmd in ("daily", "auto"):
        clips.daily(cfg) if kind == "clips" else orch.daily(cfg, force=a.force)
    elif a.cmd == "quality":
        from pipeline import quality
        print("\n".join(quality.report_lines(cfg, a.count if a.count > 1 else 7)))
    elif a.cmd == "inspect":
        from pipeline import quality
        if not a.folder:
            sys.exit("usage: python main.py inspect output/<folder>")
        print(quality.inspect(Path(a.folder).name))
    elif a.cmd == "status":
        status(cfg)
    elif a.cmd == "approve":
        folders = [j["folder"] for j in orch.awaiting()] if a.all else [Path(a.folder).name] if a.folder else []
        if not folders:
            sys.exit("usage: python main.py approve output/<folder>   |   python main.py approve --all")
        orch.approve(cfg, folders)
    elif a.cmd == "reject":
        if not a.folder:
            sys.exit('usage: python main.py reject output/<folder> --note "why"')
        orch.reject(cfg, Path(a.folder).name, a.note or "")
    elif a.cmd == "withdraw":
        if not a.folder:
            sys.exit('usage: python main.py withdraw output/<folder> --note "why"')
        orch.withdraw(cfg, Path(a.folder).name, a.note or "")
    elif a.cmd == "reschedule":
        folders = [j["folder"] for j in orch.over_cap(cfg)] if a.over_cap else [Path(a.folder).name] if a.folder else []
        if not folders:
            sys.exit("nothing to move" if a.over_cap else
                     "usage: python main.py reschedule output/<folder>   |   python main.py reschedule --over-cap")
        for f in folders:
            orch.reschedule(cfg, f, a.note or "")
    elif a.cmd == "ideas":
        from pipeline import ideas
        for t in ideas.refill_backlog(cfg):
            print(f"- [{t.get('score', '?')}] ({t.get('role', '?')}) {t['topic']}")
    elif a.cmd == "calendar":
        from pipeline import calendar_plan
        calendar_plan.build(cfg, fill=a.fill)
    elif a.cmd == "make":
        if control.killed(cfg):
            return
        for _ in range(a.count):
            if kind == "clips":
                clips.prepare_candidates(cfg, a.count)
                if not clips.make_short(cfg, segment_id=a.topic):
                    print("no rights-cleared candidate segment (see: sources)")
                    break
            else:
                orch.make_video(cfg, topic=a.topic)
    elif a.cmd == "rerender":
        import json
        if not a.folder:
            sys.exit("usage: python main.py rerender output/<folder>   (after editing its script.json)")
        orch.make_video(cfg, script=json.loads((Path(a.folder) / "script.json").read_text(encoding="utf-8")))
    elif a.cmd == "veo-prompts":
        from pipeline import ideas
        lines = [f"SAVE AS:  assets/veo/{slugify(t['topic'])}.mp4\nTOPIC:    {t['topic']}\n"
                 f"PROMPT:   {t.get('veo_prompt', '(no prompt)')}\n" for t in ideas.peek(a.count if a.count > 1 else 7)]
        out = cfg["_paths"]["output"]
        out.mkdir(exist_ok=True)
        (out / "veo_prompts.txt").write_text("\n".join(lines), encoding="utf-8")
        print("\n".join(lines))
    elif a.cmd == "publish":
        if not a.folder:
            sys.exit("usage: python main.py publish output/<folder>")
        if not control.killed(cfg):
            orch.publish(cfg, Path(a.folder).name, a.force)
    elif a.cmd == "upload-pending":
        if not control.killed(cfg):
            orch.upload_pending(cfg)
    elif a.cmd == "sync":
        orch.sync_status(cfg)
    elif a.cmd == "report":
        from pipeline import analytics
        analytics.snapshot(cfg)
        print(analytics.write_daily_report(cfg).read_text())
    elif a.cmd == "comments":
        from pipeline import comments
        comments.analyse(cfg)
        print("\n".join(comments.report_lines(cfg)) or "no public Shorts with comments yet")
    elif a.cmd == "add-music":
        folders = ([j["folder"] for j in orch.jobs.load() if j["status"] in orch.MUSIC_FIXABLE
                    and (not j.get("music") or j.get("music_not_uploaded"))]
                   if a.all else [Path(a.folder).name] if a.folder else [])
        if not folders:
            sys.exit("usage: python main.py add-music output/<folder> | --all  [--replace]")
        if not control.killed(cfg):
            for f in folders:
                orch.add_music(cfg, f, replace=a.replace)
    elif a.cmd == "crosspost":
        if not control.killed(cfg):
            orch.crosspost(cfg)
    elif a.cmd == "weekly":
        from pipeline import analytics
        analytics.fetch_video_analytics(cfg)
        print(analytics.write_weekly_report(cfg).read_text())
    elif a.cmd == "finance":
        sys.exit("the per-channel `finance` ledger is retired (it summed currencies and had no ids or backups): "
                 "use `python main.py money` (owner-level, git-ignored)")
    elif a.cmd == "auth-youtube":
        from pipeline import publish_youtube
        publish_youtube.authorize()
    elif a.cmd == "auth-analytics":
        from pipeline import analytics
        analytics.authorize()
    elif a.cmd == "channel-setup":
        from pipeline import channel
        channel.update_branding(cfg)
        if cfg.get("channel_profile", {}).get("banner"):
            channel.upload_banner(Path(cfg["channel_profile"]["banner"]))
        if cfg["youtube"].get("playlist"):
            channel.ensure_playlist(cfg["youtube"]["playlist"])
    elif a.cmd == "refresh-instagram":
        from pipeline import publish_instagram
        publish_instagram.refresh_token(cfg)
    elif a.cmd == "discover":
        from pipeline.clips import discovery
        discovery.run(cfg)
    elif a.cmd == "sources":
        sources_table(cfg)
    elif a.cmd in ("rights-approve", "rights-reject", "revoke"):
        from pipeline.clips import rights
        if not a.folder:
            sys.exit(f"usage: python main.py --channel {cfg['channel']['id']} {a.cmd} '<source id>' ...")
        if a.cmd == "rights-approve":
            rights.approve(cfg, a.folder, a.reviewer, a.note or "")
        elif a.cmd == "rights-reject":
            rights.reject(cfg, a.folder, a.reviewer, a.note or "")
        else:
            rights.revoke(cfg, a.folder, a.note or "revoked")
    elif a.cmd == "add-source":
        import yaml
        from pipeline.clips import ingest, registry
        if not (a.folder and a.rights):
            sys.exit("usage: python main.py --channel clips add-source video.mp4 --rights rights.yaml [--srt x.srt]")
        meta = yaml.safe_load(Path(a.rights).read_text(encoding="utf-8")) or {}
        sid = ingest.register_file(cfg, a.folder, meta.get("rights") or meta, a.title or meta.get("title") or
                                   Path(a.folder).stem, a.creator or meta.get("creator", "unknown"), a.srt,
                                   meta.get("url"))
        print(f"registered {sid}: {registry.get(cfg, sid)['status']}")


if __name__ == "__main__":
    main()
