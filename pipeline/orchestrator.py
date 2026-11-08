"""Principal orchestrator: production → gates → queue → upload → verification → status sync.

Agents (each a module with one job, inputs → outputs):
  ideas.py       topic intelligence: brainstorm + score topics, pick by portfolio role
  scriptgen.py   script writer + fact-checker (second, strict pass)
  youtuber.py    packaging: title, description, tags, hashtags, comment
  voice/visuals/captions/render   production
  qa.py          deterministic gate: technical, editorial/metadata, originality, rights
  review.py      AI reviewer: watches frames + script, scores, hard pass/fail
  publish_youtube.py / channel.py   publishing, verification, channel management
  analytics.py / finance.py         measurement and money
"""
import datetime as dt
import json
import os
import shutil
import traceback

from . import control, jobs
from .util import OUTPUT, slugify, read_json, write_json, log, event


# ---------------------------------------------------------------- post.txt (human-readable record)

def write_post_file(folder, script):
    yt, ig = script["youtube"], script["instagram"]
    rv, qa = script.get("review", {}), script.get("qa", {})
    lines = [
        "=== YOUTUBE ===", "TITLE:", yt["title"], "", "DESCRIPTION:", yt["description"], "",
        "TAGS:", ", ".join(yt.get("tags", [])), "",
        "COMMENT (posted automatically once public; pin it in the app):", yt.get("pinned_comment", ""), "",
        f"ROLE: {script.get('plan', {}).get('role', '?')}   PILLAR: {script.get('plan', {}).get('pillar', '?')}   "
        f"SLOT: {script.get('plan', {}).get('slot', '?')}", "",
        "=== VIRALITY CHECK ===", f"score {script.get('virality', {}).get('score', '-')}/100",
        *[f"  tip: {t}" for t in script.get("virality", {}).get("tips", [])], "",
        "=== QUALITY GATE ===", "PASS" if qa.get("pass") else "FAIL",
        *[f"  [{'ok' if c['ok'] else 'FAIL'}] {c['name']}: {c['detail']}" for c in qa.get("checks", [])], "",
        "=== AI REVIEWER ===", f"{'PASS' if rv.get('pass') else 'FAIL'}  {rv.get('scores', {})}", rv.get("summary", ""),
        *[f"- {i}" for i in rv.get("issues", [])], "",
        "=== INSTAGRAM ===", ig["caption"], "", " ".join(ig.get("hashtags", [])), "",
        "=== SOURCES ===", *[f"- {s}" for s in script.get("sources", [])], "",
        "=== FACT-CHECK LOG ===",
        *[f"[{c.get('verdict', '?')}] {c.get('claim', '')}\n    {c.get('working', '')}"
          for c in script.get("fact_checks", [])], "",
        "=== ASSET RIGHTS ===",
        *[f"scene {i}: {(s.get('rights') or {}).get('source', '?')} {(s.get('rights') or {}).get('asset_id') or ''}"
          for i, s in enumerate(script.get("scenes", []))], "",
        "=== NARRATION ===", *[s["text"] for s in script["scenes"]], "",
    ]
    (folder / "post.txt").write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------- production

def release_assets(folder):
    """A rejected or quarantined video never reached viewers: its images go back to the pool for future videos."""
    from . import visuals
    from .util import read_json
    try:
        ids = {(sc.get("rights") or {}).get("asset_id") for sc in json.loads(
            (OUTPUT / folder / "script.json").read_text(encoding="utf-8")).get("scenes", [])}
    except (OSError, ValueError):
        return
    used = read_json(visuals.USED, [])
    write_json(visuals.USED, [u for u in used if u not in ids])


MEDIA_KEYS = ("rights", "media", "media_kind", "commons_file", "reused_from", "asset")
RESUMABLE = ("voice", "visuals", "render")  # stages after the claim check whose failure keeps the verified script
_RUN = {"drafts_off": False}  # per run: a draft that deferred is not retried before a fresh topic gets the slot


def _reusable(script):
    """The verified script without anything a later stage made (visuals, voice, render, gates)."""
    out = {k: v for k, v in json.loads(json.dumps(script)).items()
           if k not in ("review", "qa", "virality", "visual_grounding", "visual_grounding_pre", "release",
                       "pipeline_state", "music")}
    for sc in out.get("scenes", []):
        for k in MEDIA_KEYS:
            if k == "commons_file" and sc.get("local_image"):
                continue  # a pinned archive image needs its licence metadata on visual-only remakes
            sc.pop(k, None)
    return out


def persist_verified(folder, script, job=None):
    """Immutable record of a script the independent claim check passed (3 Nov: a visuals failure kept only
    error.txt and the verified script, claim ledger and evidence were lost). Written once per fingerprint and made
    read-only: verified.json, or verified-<fp>.json when a later verification of changed text adds a version;
    an identical record is a no-op, so recovery and remakes are idempotent. Never written in a dry run."""
    from . import verify
    ok, how = verify.verified(script)
    if not ok or control.DRY_RUN:
        return None
    fp = script["claims_for"]
    for p in sorted(folder.glob("verified*.json")):
        if read_json(p, {}).get("fingerprint") == fp:
            return p
    path = folder / ("verified.json" if not (folder / "verified.json").exists()
                     else f"verified-{fp.split(':')[-1][:8]}.json")
    claims = script.get("claims") or []
    write_json(path, {
        "fingerprint": fp, "how": how, "verified_at": jobs.stamp(), "topic": script.get("topic"),
        "job": {k: (job or {}).get(k) for k in ("folder", "role", "slot", "created", "stages")},
        "script": _reusable(script),
        "claims": claims,
        "evidence": [{"scene": c.get("scene"), "claim": c.get("claim"), "status": c.get("status"),
                      "citation": c.get("citation"), "proposed_source": c.get("proposed_source") or c.get("source")}
                     for c in claims],
        "fact_check": script.get("fact_checks"),
        "independent_verification": script.get("independence"),
        "provenance": script.get("provenance"), "research": script.get("research"),
        "scene_plan": [{"scene": i, "text": sc.get("text"), **{k: sc.get(k) for k in
                        ("subject", "shows", "commons", "visual", "nasa") if sc.get(k)}}
                       for i, sc in enumerate(script.get("scenes", []))]})
    try:
        os.chmod(path, 0o444)  # immutable by convention and by mode; a changed script gets a new version
    except OSError:
        pass
    event("verified_persisted", folder=folder.name, file=path.name, fingerprint=fp)
    return path


def precheck_queries(text):
    """Commons searches for a story topic, most specific first: its proper nouns ("Mona Lisa Louvre", "Alexander
    Fleming"), then 3, 2 and 1 distinctive words. Deduplicated; each must have 2+ characters of real words."""
    import re
    from .visuals import key_words
    names = [re.sub(r"['’]s$", "", w) for w in re.findall(r"\b[A-Z][A-Za-z'’\-]+", text)]
    names = [w for w in names if w.lower() not in {"the", "a", "an", "in", "of", "on", "wwii", "ww2", "ww1", "wwi"}]
    kw = key_words(text)
    out = [" ".join(names[:3]), " ".join(kw[:3]), " ".join(kw[:2]), kw[0] if kw else ""]
    return [q for q in dict.fromkeys(out) if q.strip()]


MAX_VOICE_RATE = 30  # % above normal speed; faster than this stops sounding natural, so a longer script quarantines


def _faster_voice(cfg, video):
    """cfg for a remake whose voice is fast enough to bring a too-long render under video.max_seconds (pitch is
    kept by Edge TTS). Unchanged when the video wasn't too long or would need more than MAX_VOICE_RATE."""
    import copy, math
    from .util import media_duration
    try:
        dur = media_duration(video)
    except Exception:
        return cfg
    cap = cfg["video"]["max_seconds"]
    if dur <= cap + 2:  # qa allows 2 s over the cap: this was the minimum length or a different check
        return cfg
    base = float(str(cfg["voice"].get("rate", "+0%")).strip().rstrip("%") or 0)
    new = math.ceil(((1 + base / 100) * dur / (cap - 1) - 1) * 100)
    if new > MAX_VOICE_RATE:
        log(f"too long for a faster voice ({dur:.1f}s needs {new:+d}%, cap {MAX_VOICE_RATE:+d}%); remake unchanged")
        return cfg
    log(f"render was {dur:.1f}s (cap {cap}s): remaking with the voice at {new:+d}% instead of {base:+.0f}%")
    event("voice_sped_up", seconds=round(dur, 1), rate=new)
    out = copy.deepcopy(cfg)
    out["voice"]["rate"] = f"{new:+d}%"
    return out


def make_video(cfg, topic=None, script=None, slot=None):
    """Makes one video. Gate failures are remade (review.attempts) and finally quarantined; crashes are recorded
    as failed jobs instead of killing the run. Returns the job record."""
    from . import ideas
    role = jobs.role_for(cfg, slot) if slot else None
    if script:
        topic = {"topic": script["topic"], **script.get("plan", {})}
    elif isinstance(topic, str):
        topic = {"topic": topic}
    elif topic is None:
        topic = ideas.next_topic(cfg, role, drafts=not _RUN["drafts_off"])
        if cfg.get("writer", {}).get("template") == "story":  # skip stories we couldn't illustrate (no model calls)
            from .llm import Deferred
            from .visuals import commons_available
            for tries in range(1, 7):
                need = cfg.get("visual", {}).get("precheck_images", 2)
                try:
                    ok = False
                    for q in precheck_queries(topic["topic"]):  # proper nouns first (3 Nov: "discovery king"
                        found = []  # found nothing for Tutankhamun, "theft mona" nothing for the Mona Lisa)
                        if commons_available(q, need, evidence=found):
                            ok = True
                            break
                    event("visual_precheck", topic=topic["topic"], ok=ok, assets=len(found),
                          licenses=sorted({str(f.get("license")) for f in found}))
                    if ok:
                        topic["visual_precheck"] = {"at": jobs.stamp(), "need": need, "assets": found}
                        break
                except Exception as e:  # Commons down/slow: not the topic's fault, so keep it and try next run
                    write_json(ideas.BACKLOG, [topic] + read_json(ideas.BACKLOG, []))
                    raise Deferred(f"Wikimedia Commons unreachable ({type(e).__name__}); deferring to the next run")
                log(f"skipping '{topic['topic'][:60]}': no public-domain archive images found")
                if control.DRY_RUN and topic.get("score") is not None:
                    _DRY_TOPICS.append(dict(topic))  # a rehearsal's skip must not delete a real backlog topic
                if tries == 6:  # the 7th topic used to go ahead unchecked and fail at visuals after the model calls
                    raise Deferred("6 topics in a row have no public-domain archive images; deferring (refill or "
                                   "review the backlog)")
                topic = ideas.next_topic(cfg, role, drafts=not _RUN["drafts_off"])
        picked = dict(topic)  # as it sits in the backlog, draft included: what a rehearsal must give back intact
        draft, saved = topic.pop("draft", None), topic.pop("draft_saved", None)
        after = topic.pop("draft_stage", None)
        topic["_picked"] = picked
        if draft and saved and jobs.now().date() - dt.date.fromisoformat(saved) <= dt.timedelta(days=3):
            script = draft  # written and packaged before a quota deferral: resume at the claim check instead of
            # spending the writer calls again; a draft saved after a later stage failed is already verified
            # (its fingerprint is re-checked in _make_once: changed text is verified again)
            log(f"resuming a saved draft: {'verified script, its ' + after + ' stage failed' if after else 'its claim check was deferred'}")
            topic["resumed_draft"] = True
            event("draft_resumed", topic=topic["topic"], after=after)
        if control.DRY_RUN and topic.get("score") is not None:
            _DRY_TOPICS.append(picked)  # not `topic`: its draft was popped above (2 Nov: a rehearsal lost one)  # a rehearsal gives its topic back at the end of produce (not before: the
            # next video of the same rehearsal would pick the same top-scored topic again)
    attempts = cfg.get("review", {}).get("attempts", 2)
    job = None
    for attempt in range(1, attempts + 1):
        try:
            job = _make_once(cfg, topic, script, slot, role)
        except SystemExit as e:
            from .llm import Deferred
            # a resumed draft that can't be verified now (3 Nov: both drafts needed Gemini, which was down) used to
            # stop production for the whole run, every run, until the draft expired. Its draft is back in the
            # backlog (_make_once); a fresh topic, which can still have 2 independent families, gets the slot.
            if isinstance(e, Deferred) and topic.get("resumed_draft") and not _RUN["drafts_off"] \
                    and "budget" not in str(e) and getattr(e, "cause", None) != "quota":
                _RUN["drafts_off"] = True
                log(f"saved draft deferred again ({getattr(e, 'cause', None)}); kept, trying a fresh topic this run")
                event("draft_skipped", topic=topic["topic"], cause=getattr(e, "cause", None))
                return make_video(cfg, slot=slot)
            raise
        if control.DRY_RUN and job["status"] in ("failed", "quarantined"):
            # a rehearsal leaves no record: history marks a topic "done" (ideas.done_topics), so a failed rehearsal
            # used to burn a real backlog topic for good (1-2 Nov: "All the Gold Ever Mined", Saturn's rings)
            log(f"dry run: {job['status']} at '{job.get('stage_failed') or 'gates'}' ({str(job.get('reason'))[:100]}); "
                "not recorded")
            release_assets(job["folder"])
            shutil.rmtree(OUTPUT / job["folder"], ignore_errors=True)
            jobs.save([j for j in jobs.load() if j["folder"] != job["folder"]])
            return job
        if job["status"] == "dry_run":  # rehearsal output never reaches viewers: its images go back to the pool
            release_assets(job["folder"])
            if slot:
                _DRY_RESERVED.append({"folder": f"_dry_{job['folder']}", "status": "ready", "slot": slot.isoformat()})
        if job["status"] in ("ready", "awaiting_approval", "failed", "dry_run"):
            return job
        if attempt < attempts:
            log(f"gate failed ({job.get('reason')}), remaking (attempt {attempt + 1}/{attempts})")
            done = read_json(OUTPUT / job["folder"] / "script.json", {})
            # remake from the verified script (only visuals, voice and render are redone) unless the failure was
            # about the words themselves: before 2 Nov a visual or technical failure re-wrote and re-verified the
            # whole script, 3-6 strong calls for a problem the script didn't have
            accuracy = (done.get("review") or {}).get("scores", {}).get("accuracy", 10)
            if done.get("claims_for") and accuracy >= 8 and "repeated lines" not in (job.get("reason") or ""):
                script = _reusable(done)
                log("remaking with the same verified script (new visuals and render)")
                if "quality gate: duration" in (job.get("reason") or ""):
                    # the same words at the same rate come out the same length: 4 Nov the Moon Short was 65.4 s
                    # twice and was quarantined after passing every fact check, so only a faster voice can fix it
                    cfg = _faster_voice(cfg, OUTPUT / job["folder"] / "video.mp4")
            release_assets(job["folder"])
            shutil.rmtree(OUTPUT / job["folder"], ignore_errors=True)
            jobs.save([j for j in jobs.load() if j["folder"] != job["folder"]])
    log(f"quarantined {job['folder']}: kept for review, will NOT be published")
    release_assets(job["folder"])
    control.count("videos_quarantined")
    return job


def _make_once(cfg, topic, script, slot, role):
    from . import scriptgen, voice, visuals, captions, render, review, youtuber, qa

    folder = OUTPUT / f"{jobs.now():%Y-%m-%d_%H%M%S}_{slugify(topic['topic'])}"
    work = folder / "_work"
    work.mkdir(parents=True, exist_ok=True)
    job = jobs.upsert({"folder": folder.name, "topic": topic["topic"], "status": "failed", "stage": "script",
                       "role": role or topic.get("role"), "slot": slot.isoformat() if slot else None,
                       "pillar": topic.get("pillar"), "selection": topic.get("selection"),
                       "visual_precheck": topic.get("visual_precheck"),
                       "created": jobs.stamp(), "stages": {}, "youtube_id": None, "instagram_id": None})
    stage = "script"
    try:
        log(f"topic: {topic['topic']}  (role: {job['role'] or '-'}, slot: {slot or '-'})")
        if script:
            script = json.loads(json.dumps(script))
            for k in ("review", "qa"):
                script.pop(k, None)
            from . import verify
            ok, how = verify.verified(script)
            if not ok:  # hand-edited text (narration, title, headline, description, graphics) or a draft that
                stage = "verify"  # deferred before its check: a rerender never ships text the claim check hasn't seen
                log(f"{how}: verifying again")
                script = verify.check(script, cfg)
        else:
            writer = scriptgen
            if cfg.get("writer", {}).get("template") == "story":
                from . import storygen as writer  # true-story channels share everything else with the original
            script = writer.write_script(topic, cfg)
            stage = "packaging"
            youtuber.package(script, cfg)
            stage = "verify"  # after packaging, so the final title is checked too
            from . import verify
            script = verify.check(script, cfg)  # blind second opinion + arithmetic (see verify.py)
        persist_verified(folder, script, job)  # before any later stage can fail
        from . import evidence
        evidence.prune_sources(script)  # viewer-facing links must exist (outside the claim fingerprint)
        scriptgen.add_ai_disclosure(script, cfg)
        from . import virality
        virality.ensure_hashtags(script, cfg)
        script["plan"] = {"role": job["role"], "pillar": topic.get("pillar"), "series": topic.get("series"),
                          "viewer_benefit": topic.get("viewer_benefit"), "slot": job["slot"]}
        jobs.mark(folder.name, stage="script")

        stage = "voice"
        timeline, voice_wav = voice.synthesize(script["scenes"], cfg, work)
        stage = "visuals"
        for tl in timeline:  # story scripts name their main subject: every scene's second-choice image search
            tl.setdefault("commons_subject", script.get("subject_query"))
        timeline, ai_realistic = visuals.gather(timeline, cfg, work, slugify(topic["topic"]))
        from . import grounding  # deterministic per-scene check before spending a render and a review on it
        story = cfg.get("writer", {}).get("template") == "story"
        recs = grounding.records(timeline, script, story)
        pre = grounding.gate(recs, cfg, final=False)
        for n in range(cfg.get("visual", {}).get("scene_retries", 2)):
            # stage recovery (3 Nov: Pluto's verified script was discarded for one wrong picture): replace only the
            # scenes the rules reject and judge every scene again; the video goes on only when all of them pass
            bad = sorted({r["scene"] for r in recs if r["decision"] in ("MISMATCH", "UNVERIFIED")})
            if not pre or not bad:
                break
            log(f"visual grounding: replacing scene(s) {', '.join(map(str, bad))} (try {n + 1}): {'; '.join(pre)[:160]}")
            event("scenes_replaced", folder=folder.name, scenes=bad, issues=pre[:4])
            timeline, again = visuals.gather(timeline, cfg, work, slugify(topic["topic"]), only=set(bad))
            ai_realistic = ai_realistic or again
            recs = grounding.records(timeline, script, story)
            pre = grounding.gate(recs, cfg, final=False)
        # the pre-render (rules-only) record, kept apart: only the reviewer-combined one counts for release
        script["visual_grounding_pre"] = recs
        need = cfg.get("qa", {}).get("min_archive_share", 0)
        archive = sum(1 for t in timeline if (t.get("rights") or {}).get("source") == "commons")
        if need and archive < need * len(timeline):  # a story told over generic stock clips (31 Oct: all 8 scenes
            # of the Rick Rescorla story were Pexels footage) reads as a mismatch: stop before render and review
            raise ValueError(f"only {archive}/{len(timeline)} scenes have archive images of the subject "
                             f"(need {need:.0%}); the topic lacks public-domain photos")
        if pre:
            raise ValueError("visual grounding: " + "; ".join(pre)[:280])
        script["ai_visuals_realistic"] = ai_realistic
        for sc, tl in zip(script["scenes"], timeline):
            sc["rights"] = tl.get("rights")
            for k in ("asset", "commons_file", "reused_from"):  # provenance + metadata for grounding and audits
                if tl.get(k):
                    sc[k] = str(tl[k]) if k == "reused_from" else tl[k]
        stage = "render"
        yt = script.get("youtube", {})
        ass = captions.build_ass(timeline, cfg, work / "captions.ass", hook=yt.get("hook_text") or yt.get("title"))
        last = next((j.get("music") for j in reversed(jobs.load()) if j.get("music")), None)
        # background music on every video (viewers asked for it), matched to the writer's music_mood
        track = render.pick_music(cfg, avoid=last, mood=script.get("music_mood"))
        script["music"] = track.name if track else None
        video = render.render(timeline, voice_wav, ass, cfg, work, folder / "video.mp4", music=track)
        control.count("renders")
        jobs.mark(folder.name, stage="render")

        stage = "qa"
        script["virality"] = virality.score(script, sum(sc["duration"] for sc in timeline))
        script["qa"] = qa.gate(video, script, timeline, cfg, folder.name)
        failed = [c["name"] for c in script["qa"]["checks"] if not c["ok"]]
        reason = None
        if failed:
            reason = "quality gate: " + ", ".join(failed)
        elif cfg.get("review", {}).get("enabled", True):
            stage = "review"
            script["review"] = review.review_video(video, timeline, script, cfg, work)
            script["visual_grounding"] = script["review"].pop("grounding", [])
            if not script["review"]["pass"]:
                reason = "AI reviewer: " + script["review"].get("summary", "")[:120]
        write_json(folder / "script.json", script)
        write_post_file(folder, script)
        shutil.rmtree(work, ignore_errors=True)
        if not reason:  # the automated release decision, recorded now and re-checked at upload
            from . import release
            ok, checks = release.check(jobs.find(jobs.load(), folder.name), script, cfg)
            script["release"] = {"ok": ok, "checks": [{"name": n, "ok": o, "detail": d} for n, o, d in checks]}
            write_json(folder / "script.json", script)
            log(f"release decision: {'PUBLISH' if ok else 'BLOCKED: ' + release.summary(checks)}"
                + (" (dry run: nothing uploads)" if control.DRY_RUN else ""))
            if not ok:
                reason = "release gate: " + release.summary(checks)[:200]
        status = ("quarantined" if reason else
                  "dry_run" if control.DRY_RUN else  # dry-run output never uploads
                  "awaiting_approval" if cfg.get("review", {}).get("human_approval") else "ready")
        if not reason:
            control.count("videos_made")
        log(f"{'QUARANTINED' if reason else 'READY'} → {folder}" + (f" ({reason})" if reason else ""))
        return jobs.mark(folder.name, status=status, stage="gates", reason=reason, stage_failed=None,
                         title=script["youtube"]["title"], music=script.get("music"))
    except SystemExit as e:
        from .llm import Deferred
        # out of (strong-model) quota, or any other stop (a missing key, the kill switch): not this video's fault.
        # Leave no half-made job behind and give the topic back. 3 Nov: a stop that wasn't a Deferred left a
        # "failed" placeholder, even in a dry run; the next pick counted its topic as done and dropped it from the
        # backlog together with its saved draft.
        shutil.rmtree(folder, ignore_errors=True)
        jobs.save([j for j in jobs.load() if j["folder"] != folder.name])
        if topic.get("score") is not None:
            from . import ideas
            from .util import read_json
            # a rehearsal gives the topic back exactly as picked (its saved draft too: 2 Nov a dry run lost one)
            topic = dict(topic["_picked"]) if control.DRY_RUN and topic.get("_picked") else \
                {k: v for k, v in topic.items() if k != "_picked"}
            if isinstance(e, Deferred) and stage == "verify" and script and script.get("youtube") \
                    and not control.DRY_RUN:
                # keep the written, packaged script: the next run resumes at the claim check (2-3 strong
                # calls saved); never in a dry run, which must leave real state as it found it
                topic = {**topic, "draft": {k: v for k, v in script.items() if k not in ("claims", "claims_for")},
                         "draft_saved": jobs.now().date().isoformat()}
                event("draft_saved", topic=topic["topic"])
            topic = {k: v for k, v in topic.items() if k not in ("selection", "resumed_draft")}  # re-scored when picked
            write_json(ideas.BACKLOG, [topic] + read_json(ideas.BACKLOG, []))
        event("deferred" if isinstance(e, Deferred) else "production_stopped", topic=topic["topic"], stage=stage,
              reason=str(e)[:200], cause=getattr(e, "cause", None))
        raise
    except Exception as e:
        stage = getattr(e, "stage", stage)  # storygen.Unsupported: the sources don't carry the topic (stage research)
        log(f"FAILED at stage '{stage}': {e}")
        (folder / "error.txt").write_text(traceback.format_exc(), encoding="utf-8")
        shutil.rmtree(work, ignore_errors=True)
        extra = _keep_verified(cfg, folder, topic, script, stage, e)
        return jobs.mark(folder.name, status="failed", stage_failed=stage, reason=f"{type(e).__name__}: {e}"[:300],
                         **extra)


def _keep_verified(cfg, folder, topic, script, stage, err):
    """A failure after the claim check keeps the work: script.json gets the verified script with the per-scene
    visual state and where it stopped, and (voice/visuals/render, at most review.stage_resumes times) the topic
    goes back to the backlog with the verified script as its draft, so the next run starts at voice with no
    writer or verifier calls. The failed job stays in history (marked resumable: not a finished topic)."""
    from . import verify
    if not script or not verify.verified(script)[0]:
        return {}
    state = {"failed_stage": stage, "error": f"{type(err).__name__}: {err}"[:300], "at": jobs.stamp()}
    if not (folder / "script.json").exists():
        write_json(folder / "script.json", {**script, "pipeline_state": state})
    if control.DRY_RUN or stage not in RESUMABLE or topic.get("score") is None:
        return {}
    n = topic.get("draft_resumes", 0) + 1
    if n > cfg.get("review", {}).get("stage_resumes", 2):
        log(f"verified script failed at '{stage}' {n - 1} time(s) after resuming; not resuming again")
        return {}
    from . import ideas
    back = {k: v for k, v in topic.items() if k not in ("_picked", "selection", "resumed_draft")}
    back.update(draft=_reusable(script), draft_saved=jobs.now().date().isoformat(), draft_stage=stage, draft_resumes=n)
    write_json(ideas.BACKLOG, [back] + [t for t in read_json(ideas.BACKLOG, []) if t["topic"] != back["topic"]])
    event("draft_saved", topic=topic["topic"], after=stage, resumes=n)
    log(f"verified script kept: '{topic['topic'][:50]}' resumes at voice next run ({n}/"
        f"{cfg.get('review', {}).get('stage_resumes', 2)})")
    return {"resumable": True}


# ---------------------------------------------------------------- publishing

_DRY_RESERVED = []  # slots "taken" during a --dry-run (never written to disk)
_DRY_TOPICS = []    # backlog topics a --dry-run used; returned to the backlog when production ends


def publish(cfg, folder, force=False):
    """Uploads one ready job into its slot (or the next free one), then verifies it on YouTube."""
    from . import publish_youtube
    all_jobs = jobs.load()
    job = jobs.find(all_jobs, folder)
    if not job:
        raise SystemExit(f"no job record for {folder}")
    if job["status"] in jobs.LIVE:
        log(f"{folder} is already {job['status']} ({job.get('youtube_id')}): skipping")
        return job
    if job["status"] != "ready" and not force:
        log(f"not publishing {folder}: status is {job['status']} ({job.get('reason')})")
        return job
    if not cfg["youtube"].get("enabled"):
        log("youtube.enabled is false: not uploading")
        return job
    if job.get("source_id"):  # clip channel: the source's rights are re-checked at the moment of publishing
        from .clips import rights
        if not rights.is_cleared(cfg, job["source_id"]):
            log(f"not publishing {folder}: source {job['source_id']} is no longer rights-cleared")
            event("publish_blocked", folder=folder, reason="rights")
            return jobs.mark(folder, status="quarantined", reason="source no longer rights-cleared at publish time")
    if control.uploads_left(cfg) <= 0:
        log("upload limit for this run reached (control.max_uploads_per_run)")
        return job
    script = jobs.script_of(job)
    from . import verify
    ok, how = verify.verified(script)
    if cfg.get("verify", {}).get("enabled", True) and not force and not job.get("source_id") and not ok:
        # the last gate before viewers: every visible text being uploaded must be what the claim check passed
        # (a hand-edited script.json, or a video made before verify existed, would otherwise go out unchecked)
        log(f"not publishing {folder}: {how} (rerender it to re-check)")
        event("publish_blocked", folder=folder, reason="unverified", detail=how)
        return jobs.mark(folder, status="quarantined", reason=f"not verified: {how} (rerender re-verifies)")
    from . import release
    ok, checks = release.check(job, script, cfg)
    if not ok and not force:  # every release gate, re-read at the moment of upload (fails closed)
        why = release.summary(checks)
        log(f"not publishing {folder}: release gate failed ({why[:160]})")
        event("publish_blocked", folder=folder, reason="release_gate", detail=why[:300])
        return jobs.mark(folder, status="quarantined", reason=f"release gate: {why[:200]}")
    slot = (jobs.next_slot(cfg, all_jobs + _DRY_RESERVED, job.get("slot"), exclude=folder)
            if cfg["youtube"].get("schedule", True) else None)
    started = []

    def in_flight():  # persisted before videos.insert: a crash after this point is reconciled, never blindly retried
        started.append(True)
        jobs.mark(folder, status="uploading", stage="upload_started", slot=slot.isoformat() if slot else job.get("slot"))
        event("upload_started", folder=folder, slot=slot.isoformat() if slot else None)

    try:
        vid, ver = publish_youtube.upload(OUTPUT / folder / "video.mp4", script, cfg, slot,
                                          on_insert=None if control.DRY_RUN else in_flight)
    except BaseException as e:
        if started and isinstance(e, publish_youtube.QuotaExceeded):  # refused outright: nothing was uploaded
            jobs.mark(folder, status="ready", alert=None, reason="upload limit reached; retries after it resets")
        elif started:
            jobs.mark(folder, alert=f"upload outcome unknown ({type(e).__name__}): reconciled with YouTube before retry")
        event("upload_failed", folder=folder, error=f"{type(e).__name__}: {str(e)[:200]}", in_flight=bool(started))
        raise
    if control.DRY_RUN:  # remember the slot in memory only, so the rehearsal shows the real spread of slots
        if slot:
            _DRY_RESERVED.append({"folder": f"_dry_{folder}", "status": "ready", "slot": slot.isoformat()})
        return job
    owner = next((j for j in jobs.load() if j.get("youtube_id") == vid and j["folder"] != folder), None)
    if owner:  # the duplicate guard matched another job's video by title: this video was NOT uploaded
        log(f"{folder}: title matches {owner['folder']}'s video {vid}; quarantined (retitle it, then re-approve)")
        event("title_collision", folder=folder, other=owner["folder"], youtube_id=vid)
        return jobs.mark(folder, status="quarantined", reason=f"title collides with {owner['folder']} ({vid})")
    job = jobs.mark(folder, status="scheduled", stage="upload", youtube_id=vid, publish_at=slot.isoformat() if slot
                    else None, verification=ver, alert=None if ver.get("ok") else
                    f"uploaded, but YouTube shows: {', '.join(ver.get('problems', []))} (check it in Studio)")
    event("uploaded", folder=folder, youtube_id=vid, publish_at=job.get("publish_at"), verified=ver.get("ok"))
    if cfg["youtube"].get("playlist"):
        try:
            from . import channel
            channel.add_to_playlist(channel.ensure_playlist(cfg["youtube"]["playlist"]), vid)
        except Exception as e:  # playlist is nice-to-have; never undo a good upload over it
            log(f"playlist add failed: {e}")
    return job  # Instagram is a separate step (crosspost): it posts once the Short is public on YouTube


MUSIC_FIXABLE = ("ready", "awaiting_approval", "scheduled")


def add_music(cfg, folder, replace=False):
    """Background music for a video made before its channel had music: same picture and narration, a bed mixed
    under it, technical QA re-run (the voice-only file is kept as video.voice_only.mp4). A ready video is simply
    fixed before it uploads. A scheduled one is fixed locally too; with replace=True its upload, still private and
    more than 30 minutes from going public, is deleted and the new version goes into the same slot (YouTube can't
    swap a video's file). The delete comes first because the duplicate-title guard would otherwise match the old
    upload; if the new upload then fails, the job is ready and the next run's catch-up uploads it."""
    from . import qa, render
    job = jobs.find(jobs.load(), folder)
    if not job or job["status"] not in MUSIC_FIXABLE:
        log(f"{folder}: not a queued video ({job and job['status']}), music not added")
        return job
    d = OUTPUT / folder
    script = jobs.script_of(job)
    if not script.get("music"):
        track = render.pick_music(cfg, mood=script.get("music_mood"))
        if not track:
            log("this channel has no music library (video.music_dir)")
            return job
        tmp = d / "video.music.mp4"
        render.add_music(d / "video.mp4", track, cfg, tmp)
        bad = [f"{n} ({x})" for n, ok, x in qa.technical(tmp, cfg, track.name) if not ok]
        if bad:
            tmp.unlink(missing_ok=True)
            log(f"{folder}: music mix failed QA ({', '.join(bad)}), left as it was")
            return job
        (d / "video.mp4").replace(d / "video.voice_only.mp4")
        tmp.replace(d / "video.mp4")
        script["music"] = track.name
        write_json(d / "script.json", script)
        write_post_file(d, script)
        # a scheduled job's upload on YouTube is still the voice-only version until it is replaced
        job = jobs.mark(folder, music=track.name, music_not_uploaded=job["status"] == "scheduled" or None)
        log(f"{folder}: background music added ({track.name})")
    if job["status"] != "scheduled" or not job.get("music_not_uploaded") or not replace or control.DRY_RUN:
        return job
    goes_public = jobs.when(job)
    if job.get("privacy") == "public" or (goes_public and goes_public - jobs.now() < dt.timedelta(minutes=30)):
        log(f"{folder}: too close to (or past) its publish time to swap safely; it keeps the voice-only upload")
        return job
    from . import channel
    old = job["youtube_id"]
    channel.delete_video(old)
    event("replaced_upload", folder=folder, old_youtube_id=old, reason="background music added")
    jobs.mark(folder, status="ready", youtube_id=None, publish_at=None, slot=job.get("publish_at"),
              replaced_youtube_id=old, alert=None, music_not_uploaded=None)
    return publish(cfg, folder)


def crosspost(cfg):
    """Instagram Reels: every Short goes to Instagram once YouTube has made it public (same day, never before it).
    Kept apart from the YouTube upload so an Instagram failure can never block or undo a YouTube upload.
    No duplicates: the attempt is recorded before the upload, and an attempt that ended without an id (crash
    mid-upload) is never retried blindly; it is flagged for the owner to check the Instagram account."""
    ig = cfg.get("instagram") or {}
    if not ig.get("enabled") or control.DRY_RUN:
        return
    from . import publish_instagram
    if not publish_instagram.configured():
        log(f"instagram.enabled but {publish_instagram.USER_ENV} / {publish_instagram.TOKEN_ENV} are not set")
        return
    since = jobs.now() - dt.timedelta(days=ig.get("max_age_days", 3))  # first enable doesn't flood the back catalogue
    posted = 0
    for j in sorted(jobs.load(), key=lambda j: j.get("publish_at") or ""):
        if posted >= ig.get("max_per_run", 5):
            break
        if j["status"] != "published" or j.get("instagram_id") or (jobs.when(j) and jobs.when(j) < since):
            continue
        if j.get("instagram_started"):
            if not j.get("alert"):
                jobs.mark(j["folder"], alert="Instagram upload was interrupted: check the account, then set "
                                             "instagram_id (or remove instagram_started) in history.json")
            continue
        if j.get("instagram_failures", 0) >= 3:
            continue
        jobs.mark(j["folder"], instagram_started=jobs.stamp())
        try:
            media = publish_instagram.upload(OUTPUT / j["folder"] / "video.mp4", jobs.script_of(j), cfg)
        except Exception as e:  # refused before publishing (bad token, processing error): safe to retry later
            jobs.mark(j["folder"], instagram_started=None, instagram_failures=j.get("instagram_failures", 0) + 1,
                      instagram_error=f"{type(e).__name__}: {str(e)[:200]}")
            log(f"Instagram post of {j['folder']} failed: {str(e)[:160]}")
            continue
        jobs.mark(j["folder"], instagram_id=media, instagram_error=None)
        event("instagram_posted", folder=j["folder"], instagram_id=media)
        posted += 1


def awaiting(all_jobs=None):
    return [j for j in (all_jobs or jobs.load()) if j["status"] == "awaiting_approval"]


def approve(cfg, folders, upload=True):
    """Human approval: awaiting_approval → ready, then uploads straight into the planned slot."""
    for folder in folders:
        j = jobs.find(jobs.load(), folder)
        if not j or j["status"] != "awaiting_approval":
            log(f"{folder}: nothing to approve (status {j and j['status']})")
            continue
        jobs.mark(folder, status="ready", stage="approved")
        log(f"approved {folder}")
        if upload and not control.killed(cfg):
            publish(cfg, folder)


def withdraw(cfg, folder, note=""):
    """Pulls a scheduled (still private) upload before it goes public: private with no publish time on YouTube,
    status "withdrawn" here, so the slot frees up and the next run makes a replacement. Not a delete: the owner
    can still fix and reschedule it in Studio."""
    from . import channel
    j = jobs.find(jobs.load(), folder)
    if not j or j["status"] != "scheduled" or not j.get("youtube_id"):
        log(f"{folder}: only scheduled uploads can be withdrawn ({j and j['status']})")
        return j
    if control.DRY_RUN:
        log(f"dry run: would withdraw {folder} ({j['youtube_id']})")
        return j
    channel.unschedule(j["youtube_id"], cfg.get("disclosure", {}).get("ai_label", True))
    event("withdrawn", folder=folder, youtube_id=j["youtube_id"], note=note)
    release_assets(folder)
    return jobs.mark(folder, status="withdrawn", stage="withdrawn", reason=f"withdrawn: {note}".strip(": "),
                     publish_at=None, alert=None)


def reschedule(cfg, folder, note=""):
    """Moves a scheduled (still private) upload to the earliest free slot. Unlike withdraw, the video is kept and
    nothing is remade (no model calls), so it's how a lower daily target absorbs videos already on YouTube."""
    from . import channel
    j = jobs.find(jobs.load(), folder)
    lead = dt.timedelta(minutes=cfg["schedule"].get("lead_minutes", 45))
    if not j or j["status"] != "scheduled" or not j.get("youtube_id") or jobs.when(j) < jobs.now() + lead:
        log(f"{folder}: only future scheduled uploads can be rescheduled ({j and j['status']})")
        return None
    slot = jobs.next_slot(cfg, jobs.load(), exclude=folder)
    if not slot or slot == jobs.when(j):
        log(f"{folder}: no better free slot")
        return None
    if control.DRY_RUN:
        log(f"dry run: would move {folder} ({j['youtube_id']}) {jobs.when(j):%a %d %H:%M} → {slot:%a %d %H:%M}")
        return slot
    channel.reschedule(j["youtube_id"], slot, cfg.get("disclosure", {}).get("ai_label", True))
    event("rescheduled", folder=folder, youtube_id=j["youtube_id"], old=j["publish_at"], new=slot.isoformat(),
          note=note)
    jobs.mark(folder, publish_at=slot.isoformat(), slot=slot.isoformat(), role=jobs.role_for(cfg, slot))
    return slot


def over_cap(cfg, jobs_list=None):
    """Future scheduled uploads beyond schedule.videos_per_day on their day (latest first), e.g. after the
    target was lowered. Published and past videos are never counted as movable."""
    per_day = cfg["schedule"].get("videos_per_day", 3)
    lead = jobs.now() + dt.timedelta(minutes=cfg["schedule"].get("lead_minutes", 45))
    days = {}
    for j in jobs_list if jobs_list is not None else jobs.load():
        if j.get("status") in jobs.LIVE and jobs.when(j):
            days.setdefault(jobs.when(j).date(), []).append(j)
    out = []
    for day_jobs in days.values():
        day_jobs.sort(key=jobs.when)
        extra = len(day_jobs) - per_day
        movable = [j for j in day_jobs if j["status"] == "scheduled" and j.get("youtube_id") and jobs.when(j) > lead]
        out += movable[::-1][:max(extra, 0)]
    return sorted(out, key=jobs.when)


def reject(cfg, folder, note=""):
    """Human rejection: frees the slot so the next daily run makes a replacement."""
    j = jobs.mark(folder, status="rejected", stage="rejected", reason=f"rejected by reviewer: {note}".strip(": "))
    release_assets(folder)
    log(f"rejected {folder}" if j else f"{folder}: no such job")


def notify(cfg):
    """macOS notification when videos wait for approval, or when a slot in the next 24h has no video
    (with approval off, an empty slot is the failure nobody would otherwise see). No-op elsewhere."""
    n = len(awaiting())
    soon = jobs.now() + dt.timedelta(hours=24)
    empty = [s for s in jobs.free_slots(cfg, jobs.load(), 1) if s <= soon]
    msg = (f"{n} video(s) waiting for your approval. Run: python main.py status" if n else
           f"{len(empty)} slot(s) in the next 24h have no video (first {empty[0]:%a %H:%M}). "
           "Check reports/ for the reason." if empty else None)
    if msg:
        alert(cfg, msg)


def alert(cfg, msg):
    """macOS notification titled with the channel's name, plus a phone push via ntfy.sh when NTFY_TOPIC is set
    (the owner is often away from the Mac for weeks; a desktop banner alone goes unseen). No-op in dry runs.
    The topic name is the only access control on ntfy.sh, so it lives in .env and is never logged."""
    import platform
    import subprocess
    if control.DRY_RUN:
        return
    msg = msg.replace('"', "'")[:220]
    if platform.system() == "Darwin":
        subprocess.run(["osascript", "-e", f'display notification "{msg}" with title "{cfg["channel"]["name"]}"'],
                       capture_output=True)
    if topic := os.environ.get("NTFY_TOPIC"):
        import urllib.request
        req = urllib.request.Request(f"https://ntfy.sh/{topic}", data=msg.encode(), method="POST",
                                     headers={"Title": cfg["channel"]["name"].encode("ascii", "ignore").decode()})
        try:
            urllib.request.urlopen(req, timeout=15).close()
        except Exception as e:  # an alert must never break the daily run
            log(f"phone alert not sent: {type(e).__name__}")


def login_alert(cfg, error):
    """A YouTube login failure stops every upload: tell the owner right away, with the exact command."""
    if "auth-youtube" in str(error):
        flag = "" if cfg["channel"]["id"] == "cosmic" else f" --channel {cfg['channel']['id']}"
        alert(cfg, f"YouTube login needed. Run: python main.py{flag} auth-youtube")


def reconcile_uploads(cfg):
    """Jobs an interrupted run left in 'uploading': adopt the video if YouTube has it; if it still isn't there after
    control.reconcile_after_minutes, the insert never landed, so the job goes back to ready for a normal retry."""
    from . import publish_youtube
    wait = dt.timedelta(minutes=cfg.get("control", {}).get("reconcile_after_minutes", 30))
    for j in [j for j in jobs.load() if j["status"] == "uploading"]:
        if control.DRY_RUN:
            return
        vid, ver = publish_youtube.reconcile(jobs.script_of(j), cfg)
        if vid:
            log(f"reconciled {j['folder']}: it did reach YouTube as {vid}")
            jobs.mark(j["folder"], status="scheduled", stage="upload", youtube_id=vid, publish_at=j.get("slot"),
                      verification=ver, alert=None)
            event("upload_reconciled", folder=j["folder"], youtube_id=vid)
        elif jobs.now() - dt.datetime.fromisoformat(j["stages"]["upload_started"]) > wait:
            log(f"reconciled {j['folder']}: not on YouTube, back to ready")
            jobs.mark(j["folder"], status="ready", alert=None, reason="interrupted upload never reached YouTube")
            event("upload_reconciled", folder=j["folder"], youtube_id=None)


def upload_pending(cfg):
    """Uploads ready jobs, oldest first, until the run's upload limit or YouTube's quota stops it."""
    from .publish_youtube import QuotaExceeded
    try:
        reconcile_uploads(cfg)
    except QuotaExceeded as e:
        log(f"YouTube quota used up before reconciling ({e})")
        return
    for job in sorted(jobs.load(), key=lambda j: j.get("created", j["folder"])):
        if job["status"] != "ready" or not (OUTPUT / job["folder"] / "video.mp4").exists():
            continue
        if control.uploads_left(cfg) <= 0:
            break
        try:
            publish(cfg, job["folder"])
        except QuotaExceeded as e:
            log(f"YouTube quota used up, the rest stay queued for tomorrow ({e})")
            break


def sync_status(cfg):
    """Confirms what YouTube actually did: scheduled → published once public. Flags videos stuck private."""
    from . import channel
    all_jobs = jobs.load()
    live = [j for j in all_jobs if j.get("youtube_id") and j["status"] in jobs.LIVE]
    if not live or control.DRY_RUN:
        return
    found = {}
    for i in range(0, len(live), 50):
        for v in channel.video_status([j["youtube_id"] for j in live[i:i + 50]]):
            found[v["id"]] = v
    if not found:  # not one of our videos visible: a wrong/limited token or an API hiccup, not a mass deletion.
        log(f"sync: YouTube returned none of {len(live)} live video(s); not marking anything deleted")  # (marking
        event("sync_suspicious", live=len(live))  # them deleted would free their slots and remake them: duplicates)
        return
    for j in live:
        v = found.get(j["youtube_id"])
        if not v:
            j.update(status="deleted", reason="not found on YouTube (deleted outside the pipeline?)")
            continue
        privacy = v["status"]["privacyStatus"]
        j["privacy"] = privacy
        if privacy == "public" and j["status"] != "published":
            j["status"] = "published"
            j.setdefault("stages", {})["published"] = jobs.stamp()
        elif privacy == "private" and j.get("publish_at"):
            due = dt.datetime.fromisoformat(j["publish_at"]) + dt.timedelta(minutes=30)
            if jobs.now() > due:
                j["alert"] = "still private after its publish time (unaudited API project lock?)"
    jobs.save(all_jobs)


def engage(cfg):
    """Posts the packaging agent's comment on videos that are public (YouTube rejects comments on private ones)."""
    from . import channel
    if control.DRY_RUN:
        return
    all_jobs = jobs.load()
    for j in all_jobs:
        if j["status"] == "published" and not j.get("commented"):
            try:  # inside the try: one missing script.json used to abort the comments of every later video
                text = jobs.script_of(j)["youtube"].get("pinned_comment")
                if text:
                    channel.comment(j["youtube_id"], text)
                j["commented"] = True
            except Exception as e:
                log(f"comment on {j['youtube_id']} failed: {str(e)[:120]}")
    jobs.save(all_jobs)


def comment_insights(cfg):
    """Comments agent (read-only): what viewers say → guidance for the writers, packaging and ideas."""
    from . import comments
    comments.analyse(cfg)


def produce(cfg, max_videos=None):
    """Fills free slots in the next `schedule.buffer_days` days with new videos (each slot's role decides the topic).
    A topic that fails its own checks (verify, too-short script) doesn't count as a made video: the next topic gets
    the slot, up to `schedule.max_attempts_per_run` topics (31 Oct-1 Nov: two failed topics ended each run with
    quota left and the slot empty). Only infrastructure failures, or two script-stage failures in a row, stop it."""
    _RUN["drafts_off"] = False
    try:
        _produce(cfg, max_videos)
    finally:
        if control.DRY_RUN and _DRY_TOPICS:  # a rehearsal leaves the backlog as it found it
            from . import ideas
            mine = {t["topic"] for t in _DRY_TOPICS}  # the picked copies replace any version written back meanwhile
            write_json(ideas.BACKLOG, [{k: v for k, v in t.items() if k != "selection"} for t in _DRY_TOPICS]
                       + [t for t in read_json(ideas.BACKLOG, []) if t["topic"] not in mine])
            log(f"dry run: {len(_DRY_TOPICS)} topic(s) returned to the backlog")
            _DRY_TOPICS.clear()


def _produce(cfg, max_videos=None):
    max_videos = max_videos or cfg["schedule"].get("max_videos_per_run", 3)
    max_attempts = cfg["schedule"].get("max_attempts_per_run", max_videos + 2)
    made = tried = script_fails = 0
    while made < max_videos and tried < max_attempts:
        if control.killed(cfg):
            break
        if control.gemini_budget_left(cfg) < cfg.get("control", {}).get("gemini_calls_per_video", 25):
            log("not enough Gemini budget left this run for another video")
            break
        earliest = None
        if cfg.get("review", {}).get("human_approval"):  # leave the human time to check before the slot
            earliest = jobs.now() + dt.timedelta(hours=cfg["review"].get("approval_lead_hours", 12))
        free = jobs.free_slots(cfg, jobs.load() + _DRY_RESERVED, cfg["schedule"].get("buffer_days", 2),
                               earliest=earliest)
        if not free:
            log("all slots in the buffer window are filled")
            break
        job = make_video(cfg, slot=free[0])
        tried += 1
        if job["status"] != "failed":
            made += 1
        if job["status"] == "failed" and job.get("stage_failed") in ("script", "packaging"):
            script_fails += 1
            if "All Gemini models failed" in (job.get("reason") or "") or script_fails >= 2:
                log("stopping production: the script stage is failing (Gemini quota/outage?)")
                break
        else:
            script_fails = 0
    if tried >= max_attempts and made < max_videos:
        log(f"production: {tried} topic(s) tried, {made} made; attempt cap reached (schedule.max_attempts_per_run)")


def daily(cfg, force=False):
    """What the scheduler runs. Each step is independent: one failing step doesn't block the others.
    Run continuity (runs.py): an interrupted previous run and missed scheduled runs are recorded; a wake/login
    run right after a completed one is skipped (force=True runs anyway); no network → skipped, not looped."""
    if control.killed(cfg):
        return
    from . import runs
    prev = runs.start(cfg)
    runs.missed(cfg, prev)
    if not force and runs.too_soon(cfg, prev):
        log("a run finished less than control.min_minutes_between_runs ago: nothing to catch up (use --force)")
        runs.finish(cfg, "skipped_recent")
        return
    if not runs.wait_for_network(cfg):
        runs.finish(cfg, "offline")
        return
    _daily_steps(cfg)
    runs.finish(cfg)


def _health(cfg):
    from . import health
    health.notify(cfg)


def _retention(cfg):
    from . import health
    health.prune(cfg)


def release_waiting(cfg):
    """Autonomous mode (review.human_approval false): a video parked in awaiting_approval doesn't wait for a
    person. It goes through the automated release gates: pass → ready (uploads into its slot), fail →
    quarantined with the reasons. Dry runs only report the decision."""
    if cfg.get("review", {}).get("human_approval"):
        return
    from . import release
    for j in awaiting():
        try:
            script = jobs.script_of(j)
        except (OSError, ValueError) as e:
            ok, why = False, f"script.json unreadable ({type(e).__name__})"
        else:
            ok, checks = release.check(j, script, cfg)
            why = release.summary(checks)
        log(f"release decision for {j['folder']}: {'PUBLISH' if ok else 'BLOCKED: ' + why[:160]}"
            + (" (dry run: unchanged)" if control.DRY_RUN else ""))
        if control.DRY_RUN:
            continue
        event("release_decision", folder=j["folder"], ok=ok, detail=why[:300])
        if ok:
            jobs.mark(j["folder"], status="ready", stage="released")
        else:
            jobs.mark(j["folder"], status="quarantined", reason=f"release gate: {why[:200]}")
            release_assets(j["folder"])


def _daily_steps(cfg):
    from .performance import analyse as performance_review  # before production: today's videos use fresh results
    for name, step in (("sync", sync_status), ("performance review", performance_review),
                       ("release decisions", release_waiting),
                       ("catch-up uploads", upload_pending), ("production", produce),
                       ("uploads", upload_pending), ("comments", engage), ("comment insights", comment_insights),
                       ("instagram", crosspost), ("notify", notify), ("health", _health), ("retention", _retention)):
        if control.killed(cfg):
            break
        try:
            log(f"--- {name}")
            step(cfg)
        except SystemExit as e:
            log(f"{name} stopped: {e}")
            login_alert(cfg, e)
        except Exception as e:
            log(f"{name} failed: {type(e).__name__}: {e}")
    try:
        from . import analytics
        analytics.snapshot(cfg)
        try:  # once a day: watch time, retention curves, traffic sources (read-only, separate API quota)
            if analytics.analytics_due():
                analytics.fetch_video_analytics(cfg)
        except Exception as e:
            log(f"analytics v2 failed: {type(e).__name__}: {str(e)[:120]}")
        from .growth import analyse
        from . import gaps
        analyse(cfg)
        gaps.analyse(cfg)
        analytics.write_daily_report(cfg)
    except SystemExit as e:
        log(f"analytics stopped: {e}")
    except Exception as e:
        log(f"analytics failed: {type(e).__name__}: {e}")
