"""Gemini wrapper: JSON in, JSON out, with model fallback and retries.

Two tiers. `strong=True` calls (writing, fact-checking, independent verification, review) never fall back to a
weak model (gemini.weak_models, default: any "lite" model): when no strong model has quota left they raise
Deferred, and the video waits for the next run instead of being checked by a weaker model without anyone
knowing. Cheap calls (packaging, ideas, image yes/no) may use any model, and image checks use only weak models
so they can't drain the strong quota.

Exhausted models are remembered for the Pacific day (when the free quota resets) in a file shared by every
channel on the same key (keyed by a fingerprint, never the key), so later runs don't re-probe them.

Other free providers (`fallback.providers`: any OpenAI-compatible API such as Groq) take text-only calls, so one
key's ~40 strong Gemini requests a day aren't the whole pipeline's ceiling. Purposes in `fallback.prefer_for`
go to them first, which keeps Gemini for the reviewer (the only call that needs images and audio) and makes the
independent verifier a different model family from the writer. Every other text call tries them when Gemini is
out or overloaded, before deferring. A provider whose key env var is unset is skipped, so this is off by default.
Providers marked `vision: true` also take image calls (the reviewer, when Gemini is overloaded); they get the
frames only (free endpoints don't take audio), and the answer is flagged `_no_audio` so the reviewer doesn't
score sound it never heard (QA checks loudness and music deterministically anyway). `images_only: true` keeps a
small free daily allowance (OpenRouter: ~50 requests) for the reviewer instead of text calls.
"""
import datetime as dt
import hashlib
import json
import os
import random
import re
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from . import control
from .util import DATA, log, event, read_json, write_json

LAST_MODEL = None  # which model answered the latest ask_json call (writers record it: verify must use another)
EXHAUSTED = set()  # models that hit their per-day free quota (this run + earlier runs today, see _load_exhausted)
QUOTA_FILE = DATA / "gemini_quota.json"  # shared across channels on purpose: the quota belongs to the key
USAGE_FILE = DATA / "llm_usage.json"     # requests/tokens per model per Pacific day (shared like QUOTA_FILE)
_loaded = set()
# Independence is judged per model FAMILY, not per model id. 2 Nov: models.get can't resolve the alias
# "gemini-flash-latest" (it reports only "Gemini Flash Latest"), so it may be the same weights as
# "gemini-3.8-flash", and a verify on one of them after a write on the other was not a second opinion.
# Separate quota buckets (or a second API key) add capacity, never independence. independence.families
# (regex → family, first match wins) overrides these defaults; an unknown model is its own family.
FAMILIES = [(r"gemini", "gemini"), (r"gemma", "gemma"), (r"gpt-oss|openai/|gpt-", "openai"), (r"qwen", "qwen"),
            (r"llama", "llama"), (r"nemotron", "nemotron"), (r"mistral|mixtral", "mistral"), (r"deepseek", "deepseek"),
            (r"muse", "meta")]  # Meta Muse on NVIDIA (5 Nov)


def family(model, cfg=None):
    rules = list(((cfg or {}).get("independence") or {}).get("families", {}).items()) + FAMILIES
    name = str(model or "").lower()
    return next((f for pat, f in rules if re.search(pat, name)), name)


def families(models, cfg=None):
    return {family(m, cfg) for m in models if m}


class Deferred(SystemExit):
    """No allowed strong model can answer now: stop producing (not a failure) and retry at the next run.
    `cause` says which constraint (2 Nov: every deferral said "no quota", including ones caused by overload or by
    family independence, so capacity couldn't be planned): quota, overload, independence, request_size, timeout,
    budget."""

    def __init__(self, msg="", cause="quota"):
        super().__init__(msg)
        self.cause = cause


# Provider circuit breaker (per run): after llm.breaker_failures consecutive timeouts/5xx from one provider (gemini,
# groq, openrouter) it is skipped for the rest of the run instead of costing 3 × 120 s per model on every call
# (2 Nov dry runs: repeated Groq read timeouts stalled each run 6+ minutes). Rate limits and quota are not outages.
TRIPPED = set()
_FAILS = {}
ALT_FAILS = []  # kinds of fallback-provider failures in the latest ask_json (for the deferral cause)


def _outage(cfg, provider, limit=None):
    _FAILS[provider] = _FAILS.get(provider, 0) + 1
    if _FAILS[provider] >= (limit or cfg.get("llm", {}).get("breaker_failures", 2)) and provider not in TRIPPED:
        TRIPPED.add(provider)
        log(f"circuit breaker: {provider} failed {_FAILS[provider]} times in a row; skipping it for this run")
        event("provider_tripped", provider=provider, failures=_FAILS[provider])


def _healthy(provider):
    _FAILS[provider] = 0


def _gemini_outage(cfg, model, allowed):
    """Gemini overload is per model (3 Nov: flash-latest and 3.8-flash 503 all day while 3.6-flash answered in
    3 s), so its breaker counts per model ("gemini:<model>") and Gemini as a whole is tripped only once every
    allowed model is. Before, two 503s from the first two models skipped every Gemini model for the run."""
    _outage(cfg, f"gemini:{model}", limit=1)  # one 503 per model per run: failed requests cost free quota too
    if all(f"gemini:{m}" in TRIPPED for m in allowed) and "gemini" not in TRIPPED:
        TRIPPED.add("gemini")
        event("provider_tripped", provider="gemini", failures=len(allowed))


def _reopen_gemini():
    for k in [k for k in TRIPPED if k == "gemini" or k.startswith("gemini:")]:
        TRIPPED.discard(k)
        _healthy(k)


def _jitter(seconds):
    return seconds * (1 + random.random() * 0.25)  # spread retries so parallel channels don't retry in lockstep


def _quota_day():
    return dt.datetime.now(ZoneInfo("America/Los_Angeles")).date().isoformat()


def _fp(cfg):
    return hashlib.sha256((api_key(cfg) or "").encode()).hexdigest()[:10]


def _load_exhausted(cfg):
    fp = _fp(cfg)
    if fp in _loaded:
        return
    _loaded.add(fp)
    EXHAUSTED.update(read_json(QUOTA_FILE, {}).get(_quota_day(), {}).get(fp, []))


def _mark_exhausted(cfg, model):
    EXHAUSTED.add(model)
    day, fp = _quota_day(), _fp(cfg)
    data = {d: v for d, v in read_json(QUOTA_FILE, {}).items() if d >= day}  # drop past days
    data.setdefault(day, {}).setdefault(fp, [])
    if model not in data[day][fp]:
        data[day][fp].append(model)
        write_json(QUOTA_FILE, data)


def _count(cfg, model, ok, tokens=None):
    """Per-model request/token tally for the Pacific day: the cost record (all providers are free tiers, so
    tokens are the cost) and the basis for gemini.daily_limits. Failed requests count: Google charges them too."""
    day = _quota_day()
    data = {d: v for d, v in read_json(USAGE_FILE, {}).items() if d >= day}
    row = data.setdefault(day, {}).setdefault(model, {"calls": 0, "failed": 0, "tokens": 0})
    row["calls"] += 1
    row["failed"] += 0 if ok else 1
    row["tokens"] += int(tokens or 0)
    write_json(USAGE_FILE, data)


def usage_today():
    return read_json(USAGE_FILE, {}).get(_quota_day(), {})


def _over_limit(cfg, model):
    """gemini.daily_limits {model: requests}: stop at the known free limit instead of probing it (a probe of an
    exhausted model still costs a request and, for strong calls, a minute of retries)."""
    limit = (cfg.get("gemini", {}).get("daily_limits") or {}).get(model)
    if limit is not None and usage_today().get(model, {}).get("calls", 0) >= limit:  # 0 = never use it
        if model not in EXHAUSTED:
            log(f"model {model} reached its configured daily limit ({limit} requests); skipping it until the reset")
        EXHAUSTED.add(model)
        return True
    return False


def weak_models(cfg):
    g = cfg["gemini"]
    return set(g.get("weak_models") or [m for m in g["models"] if "lite" in m])


def models_for(cfg, strong=False):
    """Strong calls: strong models only. Cheap calls: weak models FIRST, so polish/packaging/comments don't spend
    the small strong quota the writer and checkers need (they did: ~20 requests/day per Flash model)."""
    _load_exhausted(cfg)
    weak = weak_models(cfg)
    ok = [m for m in cfg["gemini"]["models"] if m not in EXHAUSTED and not _over_limit(cfg, m)]
    return [m for m in ok if m not in weak] if strong else [m for m in ok if m in weak] + [m for m in ok if m not in weak]


def _extract_json(text):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"(\{.*\}|\[.*\])", text, re.S)
        if not m:
            raise
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            # Flash-Lite writes unescaped quotes inside strings (a description quoting a title): 4 packaging
            # failures 25 Oct-1 Nov, each throwing away a script the strong models had already written
            return json.loads(_escape_inner_quotes(m.group(1)))


_KEY_END = re.compile(r"\s*:")
_VALUE_END = re.compile(r"\s*(,\s*[\"\[{\d\-tfn]|[}\]]|$)")


def _escape_inner_quotes(s):
    """Escapes a double quote inside a JSON string unless it can close it: a key closes before ":", a value
    before "," + the next item, "}" , "]" or the end. Only used after strict parsing failed, so valid JSON is
    never touched."""
    out, stack, inside, is_key, prev, i = [], [], False, False, "", 0
    while i < len(s):
        ch = s[i]
        if inside:
            if ch == "\\":
                out.append(s[i:i + 2])
                i += 2
                continue
            if ch == '"':
                if (_KEY_END if is_key else _VALUE_END).match(s, i + 1):
                    inside, prev = False, '"'
                    out.append(ch)
                else:
                    out.append('\\"')
            else:
                out.append("\\n" if ch == "\n" else ch)
            i += 1
            continue
        if ch == '"':
            inside, is_key = True, bool(stack) and stack[-1] == "{" and prev in ("{", ",")
        elif ch in "{[":
            stack.append(ch)
        elif ch in "}]" and stack:
            stack.pop()
        if not ch.isspace():
            prev = ch
        out.append(ch)
        i += 1
    return "".join(out)


def api_key(cfg):
    """Each channel can use its own Gemini key (gemini.api_key_env), so one channel can't eat the other's free quota."""
    return os.environ.get(cfg["gemini"].get("api_key_env", "GEMINI_API_KEY"))


def image_has_text(path, cfg):
    """Vision check: True if the image shows readable text, labels, arrows or diagram overlays.
    Fails open (returns False) so a Gemini outage never blocks a video."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key(cfg), http_options=types.HttpOptions(timeout=60_000))
    question = ("Does this image contain any readable text, captions, labels, logos, arrows, circles drawn on it, "
                "charts, diagram elements, or a recognisable person or face? Answer only YES or NO.")
    data = open(path, "rb").read()
    mime = "image/png" if str(path).lower().endswith(".png") else "image/jpeg"
    _load_exhausted(cfg)
    for model in [cfg["gemini"].get("vision_model", "gemini-flash-lite-latest"), *sorted(weak_models(cfg))]:
        if model in EXHAUSTED:  # never the strong models: a yes/no image check isn't worth their quota
            continue
        try:
            control.count("gemini_vision_calls")
            resp = client.models.generate_content(
                model=model, contents=[types.Part.from_bytes(data=data, mime_type=mime), question],
                config=types.GenerateContentConfig(temperature=0))
            return resp.text.strip().upper().startswith("YES")
        except Exception as e:
            if "PerDay" in str(e):
                _mark_exhausted(cfg, model)
            log(f"image check on {model} failed ({str(e)[:80]})")
    log(f"image check unavailable for {Path(path).name}: accepted unchecked (the reviewer still sees the frames)")
    return False


MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".mp3": "audio/mpeg",
        ".m4a": "audio/mp4", ".wav": "audio/wav"}


def ask_json(prompt, cfg, system=None, temperature=None, images=None, strong=False, purpose="other", prefer=None,
             avoid=(), prefer_family=None):
    """images: optional list of local .jpg/.png paths sent along with the prompt (vision); an .mp3/.m4a/.wav in
    the list is sent as audio (the reviewer listens to the final mix). strong: quality-critical call (see module
    docstring); purpose: stage name for the per-stage usage log (events: llm_call); prefer: a model to try first
    (the verifier asks a different model from the writer when one has quota); avoid: models that must not answer
    (the verifier never checks text written or fact-checked by the same model: 1 Nov the Hunley script was
    fact-checked and then "independently" verified by groq gpt-oss-120b, which agreed with itself). With every
    allowed model unavailable a strong call defers rather than falling back to an avoided one.
    prefer_family: try that family's models first (2 Nov, R1: the writer's own fact-check and repair stay in the
    writer's family, so authoring uses one family and the independent check has two left instead of none)."""
    temperature = cfg["gemini"].get("temperature", 0.9) if temperature is None else temperature
    avoid = families(avoid, cfg)  # a different id of the same family is not an independent model
    alt = [(p, m) for p, m in alt_models(cfg, vision=bool(images)) if family(f"{p['name']}:{m}", cfg) not in avoid
           and p["name"] not in TRIPPED
           # a strong answer (a script, a claim ledger) needs a few thousand output tokens: a model whose free tier
           # caps output lower can't serve it (2 Nov: qwen on Groq, OTPM 1,000, refused every verify)
           and not (strong and (p.get("output_limits") or {}).get(m, 10**9) < cfg.get("llm", {}).get("strong_output_tokens", 3000))]
    ALT_FAILS.clear()
    if prefer_family:
        alt.sort(key=lambda pm: family(f"{pm[0]['name']}:{pm[1]}", cfg) != prefer_family)
        first = bool(alt) and prefer_family != "gemini" and family(f"{alt[0][0]['name']}:{alt[0][1]}", cfg) == prefer_family
    else:
        first = bool(alt) and purpose in cfg.get("fallback", {}).get("prefer_for", [])
    if first:
        out = _ask_alt(alt, prompt, system, temperature, purpose, cfg, images, stay=prefer_family)
        if out is not None:
            return out
    try:
        return _patient_gemini(prompt, cfg, system, temperature, images, strong, purpose, prefer, alt, avoid)
    except (Deferred, RuntimeError) as e:
        if alt and not first and "budget" not in str(e):
            log(f"Gemini unavailable for '{purpose}', trying the fallback providers")
            out = _ask_alt(alt, prompt, system, temperature, purpose, cfg, images)
            if out is not None:
                return out
        if isinstance(e, Deferred):
            e.cause = _cause(e.cause, alt, avoid)
            event("deferral_cause", purpose=purpose, cause=e.cause, fallback_failures=list(ALT_FAILS),
                  excluded_families=sorted(avoid))
        raise


def _cause(gemini_cause, alt, avoid):
    """Every constraint that stopped the call, Gemini's first: e.g. "overload+request_size" (2 Nov cosmic: Gemini
    503 and qwen refused the repair as too large) or "independence" (clips: no family left that didn't touch the
    text)."""
    kinds = [gemini_cause] + [k for k in ("request_size", "timeout", "connection", "overload", "quota", "invalid")
                              if k in ALT_FAILS]
    if not alt and avoid and gemini_cause in ("quota", "independence"):
        kinds[0] = "independence"
    return "+".join(dict.fromkeys(kinds))


def _patient_gemini(prompt, cfg, system, temperature, images, strong, purpose, prefer, alt, avoid=()):
    """A call no other provider can take (the reviewer's frames + audio) waits out a Gemini overload spike
    (gemini.overload_retries × overload_wait_seconds) before deferring: deferring at review throws away a
    finished render (31 Oct: both models 503 for minutes, a rendered video discarded). Quota exhaustion and
    broken answers don't wait."""
    g = cfg["gemini"]
    # only the reviewer waits: a deferral there throws away a render, while a deferred claim check keeps its draft
    # (3 Nov: a draft only Gemini could verify waited 3 × 120 s and sent 8 more calls to an overloaded Gemini)
    tries = 0 if alt or purpose not in g.get("patient_for", ["review"]) else g.get("overload_retries", 0)
    for n in range(tries + 1):
        try:
            return _ask_gemini(prompt, cfg, system, temperature, images, strong, purpose, prefer, avoid)
        except Deferred as e:
            if n == tries or "budget" in str(e) or not models_for(cfg, strong) or not _unavailable(str(e)):
                raise
            wait = g.get("overload_wait_seconds", 120)
            log(f"Gemini overloaded during '{purpose}', waiting {wait}s before retry {n + 1}/{tries}")
            time.sleep(wait)
            _reopen_gemini()  # half-open: the deliberate wait is the probe the breaker allows


def _ask_gemini(prompt, cfg, system, temperature, images, strong, purpose, prefer, avoid=()):
    global LAST_MODEL
    from google import genai
    from google.genai import types

    key = api_key(cfg)
    if not key:
        raise SystemExit(f"{cfg['gemini'].get('api_key_env', 'GEMINI_API_KEY')} missing. Get a free key at "
                         "https://aistudio.google.com/apikey and put it in .env")

    # without a timeout a stalled request hangs the whole run forever
    client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=120_000))
    gcfg = types.GenerateContentConfig(
        response_mime_type="application/json",
        temperature=temperature,
        system_instruction=system,
    )
    contents = prompt
    if images:
        contents = [types.Part.from_bytes(data=open(p, "rb").read(), mime_type=MIME.get(Path(p).suffix.lower(),
                                                                                   "image/jpeg"))
                    for p in images] + [prompt]
    last_err = None
    allowed = models_for(cfg, strong)
    candidates = [m for m in allowed if family(m, cfg) not in avoid]
    if prefer in candidates:
        candidates = [prefer] + [m for m in candidates if m != prefer]
    candidates = [m for m in candidates if f"gemini:{m}" not in TRIPPED]
    if "gemini" in TRIPPED or (allowed and all(f"gemini:{m}" in TRIPPED for m in allowed)):
        TRIPPED.add("gemini")
        candidates = []
    if strong and not candidates:
        if "gemini" in TRIPPED:
            raise Deferred(f"Gemini skipped for '{purpose}' (circuit breaker: repeated overload this run)", "overload")
        if allowed:
            raise Deferred(f"no independent model family is available for '{purpose}': every allowed family wrote, "
                           f"fact-checked or repaired the text ({', '.join(sorted(avoid))}); deferring", "independence")
        raise Deferred(f"no strong Gemini model has free quota left for '{purpose}' today; deferring to the next "
                       "run (not falling back to a weaker model)", "quota")
    for model in candidates:
        if model in EXHAUSTED:  # exhausted earlier in this loop's lifetime
            continue
        for attempt in range(3):
            try:
                if control.gemini_budget_left(cfg) <= 0:
                    raise Deferred("Gemini call budget for this run used up (control.max_gemini_calls_per_run)")
                if _over_limit(cfg, model):
                    break
                control.count("gemini_calls")
                t0 = time.monotonic()
                try:
                    resp = client.models.generate_content(model=model, contents=contents, config=gcfg)
                except Exception:
                    _count(cfg, model, ok=False)
                    raise
                um = getattr(resp, "usage_metadata", None)
                _count(cfg, model, ok=True, tokens=getattr(um, "total_token_count", None))
                _healthy(f"gemini:{model}")
                out = _extract_json(resp.text)
                event("llm_call", purpose=purpose, model=model, ok=True, seconds=round(time.monotonic() - t0, 1),
                      attempt=attempt + 1, tokens=getattr(um, "total_token_count", None))
                LAST_MODEL = model
                return out
            except Exception as e:  # rate limit, bad model name, bad JSON...
                last_err = e
                msg = str(e)
                if "404" in msg or "not found" in msg.lower():
                    log(f"model {model} not available, trying next")
                    break
                if "PerDay" in msg:
                    log(f"model {model} used up its daily free quota, skipping it until the quota resets")
                    _mark_exhausted(cfg, model)
                    break
                transient = (any(c in msg for c in ("500 INTERNAL", "502", "503", "504"))
                             or "timed out" in msg.lower() or "disconnected" in msg.lower())
                if transient:  # overloaded/slow: these failures still count against the 20/day quota (31 Oct: both
                    log(f"model {model} overloaded or timing out, trying next")  # models "used up" with 0 successes)
                    _gemini_outage(cfg, model, allowed)
                    break
                wait = _jitter(20 * (attempt + 1) if "429" in msg else 3)
                log(f"Gemini error on {model} ({msg[:120]}), retrying in {wait}s")
                time.sleep(wait)
    event("llm_call", purpose=purpose, ok=False, error=f"{type(last_err).__name__}: {str(last_err)[:160]}")
    if strong and (not models_for(cfg, strong) or _unavailable(last_err)):
        # out of quota or Google overloaded: not the topic's fault, so the video waits instead of failing (a
        # failed job also burns its topic). A bad answer (broken JSON) still fails.
        m = str(last_err).lower()
        raise Deferred(f"strong Gemini models unavailable during '{purpose}' ({str(last_err)[:80]}); "
                       "deferring to the next run",
                       "quota" if ("429" in m or "exhausted" in m or not models_for(cfg, strong)) else
                       "timeout" if ("timed out" in m or "disconnected" in m) else "overload")
    raise RuntimeError(f"All Gemini models failed: {last_err}")


def _unavailable(err):
    msg = str(err).lower()
    return any(k in msg for k in ("429", "500 internal", "502", "503", "504", "timed out", "disconnected",
                                  "unavailable", "exhausted"))  # Google-side errors: defer, don't burn the topic


def alt_models(cfg, vision=False):
    """[(provider, model)] for the configured OpenAI-compatible providers whose key is set, minus exhausted ones:
    vision providers for image calls, the others (not images_only) for text calls."""
    out = []
    used = usage_today()
    for p in cfg.get("fallback", {}).get("providers", []):
        fits = p.get("vision") if vision else not p.get("images_only")
        cap = p.get("daily_requests")  # a free account's small daily allowance is shared (OpenRouter ~50/day)
        if cap is not None and sum(v.get("calls", 0) for k, v in used.items() if k.startswith(p["name"] + ":")) >= cap:
            continue
        if fits and os.environ.get(p.get("key_env", "")):
            out += [(p, m) for m in p.get("models", []) if f"{p['name']}:{m}" not in EXHAUSTED]
    return out


def _post(p, body):
    """One chat/completions request. stream: true providers get the answer as server-sent chunks, put back together
    into the usual response shape: 5 Nov, NVIDIA's free queue closed idle connections ("Remote end closed
    connection without response") while Mistral was still writing a claim ledger, so every verify failed."""
    url = p["base_url"].rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {os.environ[p['key_env']]}"}
    if not p.get("stream"):
        return requests.post(url, json=body, timeout=p.get("timeout", 120), headers=headers)
    # a healthy stream sends a chunk every second or two: a long silence is a stalled request, not a slow answer
    # (5 Nov: both runs sat 24 min on silent NVIDIA streams with the 900 s timeout meant for whole answers)
    r = requests.post(url, json={**body, "stream": True}, timeout=(30, p.get("stream_idle", 180)), headers=headers,
                      stream=True)
    if r.status_code != 200:
        return r
    r.encoding = "utf-8"  # event streams name no charset: requests fell back to Latin-1 ("Titanic’s" → "Titanicâs")
    parts, usage = [], None
    # a stream that never goes quiet can still run forever: 5 Nov, Mistral sent reasoning chunks for 17+ min, so
    # the idle limit never fired. The provider's timeout caps the whole answer.
    deadline = time.monotonic() + p.get("timeout", 900)
    for line in r.iter_lines(decode_unicode=True):
        if time.monotonic() > deadline:
            r.close()
            raise requests.exceptions.ReadTimeout(f"stream ran past {p.get('timeout', 900)} s")
        if not line or not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        chunk = json.loads(data)
        usage = chunk.get("usage") or usage
        for ch in chunk.get("choices") or []:
            parts.append((ch.get("delta") or {}).get("content") or "")
    whole = {"choices": [{"message": {"content": "".join(parts)}}], "usage": usage}
    return type("Streamed", (), {"status_code": 200, "text": "".join(parts)[:200], "json": lambda self: whole})()


def _ask_alt(alt, prompt, system, temperature, purpose, cfg, images=None, stay=None):
    global LAST_MODEL
    """Chat-completions call with JSON mode. Returns None (logged) when every provider model fails, so the caller
    falls back or defers; never raises for provider errors. The key goes only in the Authorization header.
    stay: the family the call must keep (prefer_family): a per-minute limit is waited out rather than handing the
    call to another family's model (3 Nov cosmic: gpt-oss 429 at fact_check moved to nemotron, leaving verify only
    the tripped Gemini)."""
    import base64
    pics = [p for p in images or [] if MIME.get(Path(p).suffix.lower(), "image/jpeg").startswith("image/")]
    no_audio = len(pics) < len(images or [])
    content = prompt
    if images:
        note = "\n(No audio is attached: leave out the audio score.)" if no_audio else ""
        content = [{"type": "text", "text": prompt + note}] + [
            {"type": "image_url", "image_url": {"url": f"data:{MIME.get(Path(p).suffix.lower(), 'image/jpeg')};base64,"
                                                      + base64.b64encode(open(p, "rb").read()).decode()}} for p in pics]
    messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": content}]
    for i, (p, model) in enumerate(alt):
        if p["name"] in TRIPPED:  # circuit breaker: this provider failed repeatedly in this run
            continue
        name = f"{p['name']}:{model}"
        body = {"model": model, "messages": messages, "temperature": temperature,
                **({"response_format": {"type": "json_object"}} if p.get("json_mode", True) else {}),
                **(p.get("extra_body") or {})}  # local Ollama: no JSON mode (501), thinking off (4 min → 2 s)
        if p.get("tpm"):  # Groq counts prompt + *requested* completion against its per-minute token limit; with no
            # max_tokens it assumes a large default and refuses a 3k-token verify as "too large" (2 Nov: qwen, TPM
            # 8,000 per x-ratelimit-limit-tokens). Ask only for what fits: never truncates a typical answer (~2k).
            est = (len(json.dumps(messages)) + 3) // 3
            body["max_tokens"] = max(1024, min(p.get("max_tokens", 4096), p["tpm"] - est - 300))
        for attempt in range(3):
            if control.gemini_budget_left(cfg) <= 0:  # same runaway guard as Gemini calls
                raise Deferred("LLM call budget for this run used up (control.max_gemini_calls_per_run)")
            control.count("gemini_calls")
            t0 = time.monotonic()
            try:
                r = _post(p, body)
                _count(cfg, name, ok=r.status_code == 200, tokens=(r.json().get("usage") or {}).get("total_tokens")
                       if r.status_code == 200 else None)
                if r.status_code == 200:
                    text = r.json()["choices"][0]["message"].get("content")
                    if not (text or "").strip():  # reasoning models can spend the whole budget thinking and answer
                        # null (4 Nov: nemotron-3-ultra, 3 retries per call); asking again gets the same: next model
                        ALT_FAILS.append("invalid")
                        log(f"{name} returned an empty answer, trying the next model")
                        break
                    out = _extract_json(text)
                    if no_audio and isinstance(out, dict):
                        out["_no_audio"] = True
                    usage = r.json().get("usage") or {}
                    event("llm_call", purpose=purpose, model=name, ok=True, seconds=round(time.monotonic() - t0, 1),
                          attempt=attempt + 1, tokens=usage.get("total_tokens"))
                    LAST_MODEL = name
                    _healthy(p["name"])
                    return out
                msg = f"{r.status_code} {r.text[:200]}"
            except Exception as e:  # network, timeout, bad JSON
                msg = f"{type(e).__name__}: {str(e)[:200]}"
            low = msg.lower()
            if any(k in low for k in ("timeout", "timed out", "connectionerror", "max retries")) or msg[:3] in ("500", "502", "503", "504"):
                ALT_FAILS.append("timeout" if "time" in low else "connection" if ("ssl" in low or "connection" in low
                                                                                 or "max retries" in low) else "overload")
                _outage(cfg, p["name"])
                if p["name"] in TRIPPED:
                    break
            elif "request too large" in low or "413" in msg[:3]:
                ALT_FAILS.append("request_size")
            elif msg.startswith("429") or "per day" in low:
                ALT_FAILS.append("quota")
            elif "json" in low:
                ALT_FAILS.append("invalid")
            if "per day" in msg.lower() or "(rpd)" in msg.lower() or "(tpd)" in msg.lower():
                log(f"{name} used up its daily free quota, skipping it this run")
                EXHAUSTED.add(name)
                break
            if msg.startswith("400") and ("response_format" in msg or "failed to generate json" in msg.lower()) \
                    and body.pop("response_format", None):
                continue  # strict JSON mode failed: ask again with the prompt's "Return JSON" and parse it ourselves
            if msg.startswith(("400", "401", "402", "403", "404")):
                log(f"{name} rejected the request ({msg[:120]}), trying next")
                break
            if "request too large" in msg.lower():  # bigger than this model's per-minute token limit: waiting can't help
                log(f"{name} can't take a request this size, trying the next model")
                break
            hint = re.search(r"try again in ([\d.]+)s", msg)
            nxt = alt[i + 1] if i + 1 < len(alt) else None
            keep = stay and family(name, cfg) == stay and family(f"{nxt[0]['name']}:{nxt[1]}", cfg) != stay if nxt else False
            if msg.startswith("429") and nxt and not keep and not (hint and float(hint.group(1)) <= 30):
                log(f"{name} is at its per-minute limit, trying the next model")  # each model has its own bucket
                break  # a short hinted wait is better spent on this (larger) model: the next may be too small
            # the provider's own hint is exact; only our backoff guesses get jitter
            wait = min(60, float(hint.group(1)) + 1) if hint else _jitter(20 * (attempt + 1) if msg.startswith("429") else 3)
            log(f"{name} error ({msg[:120]}), retrying in {wait:.0f}s")
            time.sleep(wait)
    event("llm_call", purpose=purpose, model="fallback", ok=False)
    log(f"fallback providers failed for '{purpose}'")
    return None
