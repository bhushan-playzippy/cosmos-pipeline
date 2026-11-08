"""Document evidence for the claim check: the Wikipedia articles on the script's subject (free API, no key),
used for two things the verifier model can't be trusted with on its own:
  1. attaching a quoted sentence that states the narrated number, so a VERIFIED claim cites text, not the
     model's confidence;
  2. catching a narrated number that the article contradicts: a sentence about the same thing (shares content
     words with the scene) gives the same unit a value outside tolerance, and the article never states the
     narrated value. 1 Nov: the Hunley article says the torpedo held "135 pounds (61 kilograms) of black powder"
     on a "22-foot" spar; the Short said "five-foot" and "80 pounds", and the verifier model agreed with itself.
Wikipedia is a tertiary source: agreement is supporting evidence, a contradiction sends the claim to repair with
the article's value as a hint, and the repair must pass the full re-check. Unreachable → no document evidence
(recorded), never a pass by default.
"""
import re

import requests

from . import facts
from .util import log

API = "https://en.wikipedia.org/w/api.php"
HEADERS = {"User-Agent": "cosmos-pipeline-factcheck/1.1 (educational Shorts fact-checking; "
                         "https://www.youtube.com/@peakmomentsworld) python-requests"}  # Wikimedia wants a contact
_CACHE = {}
_LAST = [0.0]      # time of the last Wikipedia API call (paces bursts: research + claim check fetch many pages)
MIN_GAP = 1.0      # seconds between calls
TRIES = 5          # 429 "too many requests" answers, each waited out (Retry-After, else exponential backoff)


def _api(params):
    """One Wikipedia API call, paced and retried. 4 Nov: Wikipedia answered 429 with Retry-After: 2; the old code
    read the empty body as bad JSON, waited 3 s once and gave up, so research saw no articles and rejected true
    stories ("the sources don't support this story": the Titanic wreck, Apollo 11's 1202 alarm)."""
    import random, time
    for attempt in range(TRIES):
        wait = _LAST[0] + MIN_GAP - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _LAST[0] = time.monotonic()
        r = requests.get(API, params={**params, "format": "json"}, headers=HEADERS, timeout=20)
        if r.status_code == 429 or r.status_code >= 500:
            hint = r.headers.get("retry-after")
            pause = float(hint) if hint and hint.replace(".", "", 1).isdigit() else 2 ** attempt
            time.sleep(min(30.0, pause) * random.uniform(1.0, 1.25))
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError(f"Wikipedia still throttling after {TRIES} tries")  # query → [(title, url, [sentences])] for this run only (per-channel safe: nothing is written)
STOP = {"about", "after", "again", "their", "there", "these", "those", "which", "while", "would", "could", "every",
        "other", "than", "then", "that", "this", "with", "from", "into", "over", "under", "your", "they", "were",
        "what", "when", "where", "only", "first", "still", "just", "even", "more", "most", "much", "very", "some"}


def _words(text):
    return {w for w in re.findall(r"[a-z]{4,}", str(text).lower()) if w not in STOP and w not in facts.UNITS
            and w not in facts.WORDS and w not in facts.MONTHS}


def reference(query, pages=2):
    """[(title, url, sentences)] for the top Wikipedia matches of `query`, or [] when Wikipedia is unreachable."""
    if not query:
        return []
    if query in _CACHE:
        return _CACHE[query]
    import time
    for attempt in range(2):  # Wikipedia throttles bursts with an empty body (3 Nov): one paced retry
        out = []
        try:
            hits = _api({"action": "query", "list": "search", "srsearch": query, "srlimit": pages})["query"]["search"]
            for h in hits:
                page = _api({"action": "query", "prop": "extracts", "explaintext": 1, "titles": h["title"],
                             "redirects": 1})["query"]["pages"]
                text = next(iter(page.values())).get("extract", "")
                sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text) if 20 < len(s.strip()) < 600]
                out.append((h["title"], "https://en.wikipedia.org/wiki/" + h["title"].replace(" ", "_"), sentences))
            _CACHE[query] = out  # only an answer is cached: a throttled call is tried again later in the run
            return out
        except Exception as e:  # offline, rate-limited, bad JSON: no document evidence this time
            if attempt == 0:
                time.sleep(3)
                continue
            log(f"evidence: Wikipedia unavailable ({type(e).__name__}); claims rest on the verifier alone")
    return []


def assess(scene_text, docs, tol):
    """For each (value, unit) the scene states: ("support", quote, url) when a relevant sentence states it,
    ("conflict", quote, url, doc_values) when relevant sentences give that unit only other values, else None."""
    mine = _words(scene_text)
    results = []
    for v, unit in facts.quantities(scene_text):
        support = conflict = None
        for title, url, sentences in docs:
            for s in sentences:
                shared = mine & _words(s)
                if len(shared) < 2 and not (unit is None and v >= 1000 and len(shared) >= 1):
                    continue
                qs = facts.quantities(s)
                # same unit on both sides: "five-foot" ≠ "five crewmen", and a unit-less "40 million atmospheres"
                # (facts doesn't know atm/psi) is not supported by "40 million years" (3 Nov: Jupiter's core
                # pressure was cited to the Marine life article); a unit-less match also needs 2 shared words
                if any(facts.close(v, d, 0.02) and u == unit for d, u in qs) and (unit or len(shared) >= 2):
                    support = support or ("support", s[:300], url)
                elif unit and len(shared) >= 3:
                    others = [d for d, u in qs if u == unit]
                    if others and not any(facts.close(v, d, tol) for d in others):
                        conflict = conflict or ("conflict", s[:300], url, others)
        results.append(((v, unit), support or conflict))
    return results


# ---------------------------------------------------------------- non-numeric claims (2 Nov)
# A model-named source is a lead, never a citation: a page is only cited when it was fetched here and a sentence on
# it contains the claim's key terms (or the exact quoted words). Only https pages on these domains are fetched
# (text only, size and time capped); others are recorded as "proposed, not checked". First match wins.
QUALITY = [
    (r"(^|\.)(nasa\.gov|esa\.int|noaa\.gov|nps\.gov|loc\.gov|archives\.gov|si\.edu|navy\.mil|army\.mil|af\.mil|"
     r"usgs\.gov|nih\.gov|nationalarchives\.gov\.uk|bl\.uk|europeana\.eu|eso\.org|noirlab\.edu|cern\.ch)$",
     "primary/institutional"),
    (r"(^|\.)(edu|ac\.uk|nature\.com|science\.org|aps\.org|iop\.org|arxiv\.org|jstor\.org|aanda\.org)$", "scholarly"),
    (r"(^|\.)(britannica\.com|bbc\.co\.uk|bbc\.com|smithsonianmag\.com|nationalgeographic\.com|history\.com|"
     r"hunley\.org|rmg\.co\.uk|iwm\.org\.uk|metmuseum\.org|nypl\.org|space\.com|theguardian\.com|nytimes\.com)$",
     "reputable secondary"),
    (r"(^|\.)wikipedia\.org$", "tertiary"),
]
_PAGES = {}  # url → (title, url, sentences) or None, this run only


def source_quality(url):
    from urllib.parse import urlparse
    try:
        u = urlparse(str(url or ""))
    except ValueError:
        return None
    host = (u.hostname or "").lower()
    if u.scheme != "https" or not host:
        return None
    return next((q for pat, q in QUALITY if re.search(pat, host)), None)


def fetch(url, max_bytes=2_000_000):
    """(title, url, sentences) of an allowlisted https page, or None. Page text is data, never instructions."""
    if url in _PAGES:
        return _PAGES[url]
    page = None
    if source_quality(url):
        try:
            with requests.get(url, headers=HEADERS, timeout=20, stream=True, allow_redirects=True) as r:
                ctype = r.headers.get("content-type", "")
                if r.status_code == 200 and source_quality(r.url) and ("html" in ctype or "text/plain" in ctype):
                    raw = r.raw.read(max_bytes, decode_content=True).decode(r.encoding or "utf-8", "replace")
                    title = re.search(r"<title[^>]*>(.*?)</title>", raw, re.S | re.I)
                    text = re.sub(r"<(script|style|nav|footer|header)[^>]*>.*?</\1>", " ", raw, flags=re.S | re.I)
                    text = re.sub(r"&nbsp;|&#160;", " ", re.sub(r"<[^>]+>", "\n", text))
                    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text) if 20 < len(s.strip()) < 600]
                    page = ((title.group(1).strip()[:150] if title else url), r.url, sentences)
        except Exception as e:  # unreachable: the source stays "proposed, not confirmed"
            log(f"evidence: could not fetch {url[:80]} ({type(e).__name__})")
    _PAGES[url] = page
    return page


def _norm(text):
    """Lower-case words, accents folded: 5 Nov "Möhne" became "m hne" and never met a key term "Mohne" (Dambusters:
    "the Möhne and Edersee dams were breached" counted as no document evidence)."""
    import unicodedata
    text = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", text.lower().replace("’", "'"))).strip()


def _has(term, sent_words):
    """Every word of a key term in the sentence, allowing simple inflection (kill/killed/killing)."""
    ws = [w for w in _norm(term).split() if w]
    return bool(ws) and all(any(x == w or (len(w) >= 4 and x.startswith(w[:max(4, len(w) - 2)])) for x in sent_words)
                            for w in ws)


def passage(terms, docs, quote=None):
    """Best evidence for a non-numeric claim: ("direct", sentence(s), title, url) when one sentence or two
    neighbouring ones contain every key term (for a quote: the exact quoted words), ("indirect", ...) when the
    terms are all on the page but not together, else None."""
    terms = [t for t in terms or [] if _norm(t)]
    best = None
    for title, url, sentences in docs:
        if quote and len(_norm(quote).split()) >= 3:
            q = _norm(quote)
            hit = next((s for s in sentences if q in _norm(s)), None)
            if hit:
                return ("direct", hit[:400], title, url)
            continue  # a quote is only supported by its own words
        if not terms:
            continue
        norm = [_norm(s).split() for s in sentences]
        for k in range(len(sentences)):
            win = norm[k] + (norm[k + 1] if k + 1 < len(norm) else [])
            if all(_has(t, win) for t in terms):
                return ("direct", " ".join(sentences[k:k + 2])[:400], title, url)
        page_words = [w for ws in norm for w in ws]
        if best is None and all(_has(t, page_words) for t in terms):
            k = max(range(len(sentences)), key=lambda k: sum(_has(t, norm[k]) for t in terms))
            best = ("indirect", sentences[k][:400], title, url)
    return best


def publisher(url):
    from urllib.parse import urlparse
    host = (urlparse(str(url or "")).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def pages(urls, limit=6):
    """Fetched allowlisted pages for the URLs the checker proposed (at most `limit`), as extra documents."""
    out = []
    for u in list(dict.fromkeys(u for u in urls if u))[:limit]:
        page = fetch(u)
        if page:
            out.append(page)
    return out


# ---------------------------------------------------------------- source links shown to viewers (3 Nov)
URL = re.compile(r"https?://[^\s\]\"'>,]+")


def _urls(text):
    """Extract links without truncating balanced Wikipedia parentheses or absorbing prose punctuation."""
    out = []
    for url in URL.findall(str(text)):
        url = url.rstrip(".")
        while url.endswith(")") and url.count(")") > url.count("("):
            url = url[:-1]
        out.append(url)
    return out


def dead_link(url):
    """True only when the page definitely isn't there (404/410, or the host doesn't exist). A 403/5xx/timeout is
    not proof (sites block bots, have outages), so the link stays."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=12, allow_redirects=True, stream=True)
        r.close()
        return r.status_code in (404, 410)
    except requests.exceptions.ConnectionError as e:
        return "resolve" in str(e).lower() or "nodename" in str(e).lower() or "name or service" in str(e).lower()
    except Exception:
        return False


def prune_sources(script, check=None):
    """Drops source links that don't exist from script["sources"] and the description's "Sources:" part (3 Nov: the
    Jupiter description cited https://www.noaa.gov/mariana-trench, a 404 the writer invented). If nothing is
    left, the pages the claim check actually fetched and quoted are cited instead. The "Sources:" part is outside
    the claim fingerprint (facts._body), so this never invalidates the verification. Returns the dropped URLs."""
    check = check or dead_link
    yt = script.get("youtube") or {}
    desc = str(yt.get("description") or "")
    head, sep, tail = (re.split(r"(\n\s*sources?\s*:)", desc, maxsplit=1, flags=re.I) + ["", ""])[:3]
    urls = list(dict.fromkeys(_urls(tail) + [u for s in script.get("sources", []) for u in _urls(s)]))
    dead = [u for u in urls if check(u)]
    if not dead:
        return []
    keep = [s for s in script.get("sources", []) if not any(u in str(s) for u in dead)]
    if not keep:
        keep = list(dict.fromkeys(f"{(c.get('citation') or {}).get('title')} ({c['citation']['url']})"
                                  for c in script.get("claims", []) if (c.get("citation") or {}).get("url")))[:3]
    script["sources"] = keep
    if sep:
        rest = tail.split("\n\n", 1)  # the list ends at the first blank line; AI label and hashtags follow
        listed = ", ".join(keep) if keep else "see the pinned comment"
        yt["description"] = head + sep + " " + listed + ("\n\n" + rest[1] if len(rest) > 1 else "")
    log(f"sources: dropped {len(dead)} link(s) that don't exist: {', '.join(dead)[:160]}")
    return dead
