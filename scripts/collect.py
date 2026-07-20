"""
Stage 1: gather, cluster, rank, and hand off to Claude for translation.

Deliberately deterministic end to end. Everything a language model decides
happens in stage 2; if this script's output changes, it is because the feeds
changed, not because a model sampled differently.
"""
import argparse
import concurrent.futures
import json
import os
import re
import ssl
import sys
import urllib.request
from datetime import datetime, timezone, timedelta

import feedparser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import entities as ent
from feeds import FEEDS
from vocab import TAGS

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(REPO_ROOT, "data")
JST = timezone(timedelta(hours=9))

SEEN_PATH = os.path.join(DATA_DIR, "seen_index.json")
ITEMS_PATH = os.path.join(DATA_DIR, "pending_items.json")

SEEN_RETENTION_DAYS = 90
DEFAULT_LIMIT = 70
TIER_A_COUNT = 15
MAX_ENTRIES_PER_FEED = 80
FETCH_TIMEOUT = 30
FETCH_WORKERS = 8

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ai-collect/1.0"


# --- news-digest interop -----------------------------------------------------
# One-way dependency: ai-collect reads from news-digest, never writes to it.
def _load_news_digest_helpers():
    path = os.environ.get("NEWS_DIGEST_SCRIPTS", r"C:\dev\news-digest\scripts")
    if not os.path.isdir(path):
        raise SystemExit(
            "news-digest scripts not found at {!r}. Set NEWS_DIGEST_SCRIPTS to "
            "its scripts directory.".format(path)
        )
    if path not in sys.path:
        sys.path.insert(0, path)
    try:
        import generate_digest as gd
    except ImportError as e:
        raise SystemExit("Could not import generate_digest from {}: {}".format(path, e))
    missing = [n for n in ("domain_of", "tokenize", "similarity", "cluster_articles",
                          "is_junk", "ai_relevance") if not hasattr(gd, n)]
    if missing:
        raise SystemExit(
            "generate_digest is missing {}. Its API changed; update collect.py "
            "(see tests/test_contract.py).".format(", ".join(missing))
        )
    return gd


gd = _load_news_digest_helpers()

# news-digest clusters at 0.5 because a digest *wants* one entry per story and
# an over-merge just loses a duplicate. An archive is the opposite: merging two
# genuinely different stories deletes one of them permanently. Observed at 0.5:
# "Claude make Fable 5 permanent" and a community fine-tune post sharing only
# {claude, fable} merged at exactly 0.5.
# Module-scope assignment, but nothing else runs in this process and
# news-digest is never invoked from here, so the morning job is unaffected.
gd.OVERLAP_THRESHOLD = 0.6


# --- fetching ----------------------------------------------------------------
def _ssl_context():
    """certifi bundle, not the system store.

    arxiv.org serves an intermediate the Windows store will not complete, and
    the resulting handshake failure looks exactly like an empty feed.
    """
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


_SSL_CTX = _ssl_context()


def _clean_summary(entry):
    text = entry.get("summary", "") or ""
    text = re.sub(r"<[^<]+?>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:600]


def fetch_feed(feed_def):
    """Returns (articles, ok, error). Never raises."""
    url = feed_def["url"]
    cutoff = datetime.now(timezone.utc) - timedelta(hours=feed_def["window_hours"])
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT, context=_SSL_CTX) as r:
            raw = r.read()
    except Exception as e:
        return [], False, "{}: {}".format(type(e).__name__, e)

    parsed = feedparser.parse(raw)
    out = []
    for entry in parsed.entries[:MAX_ENTRIES_PER_FEED]:
        stamp = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
        published = None
        if stamp:
            try:
                published = datetime(*stamp[:6], tzinfo=timezone.utc)
            except (TypeError, ValueError):
                pass
        if published is not None and published < cutoff:
            continue
        title = (entry.get("title") or "").strip()
        link = (entry.get("link") or "").strip()
        if not title or not link:
            continue
        out.append({
            "title": title,
            "link": link,
            "summary": _clean_summary(entry),
            "domain": gd.domain_of(link),
            "published": published,
            "source_label": feed_def["label"],
            "category": feed_def["category"],
            "primary": feed_def["primary"],
            "weight": feed_def["weight"],
        })
    return out, True, ""


def collect_all():
    articles, status = [], []
    with concurrent.futures.ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
        futures = {pool.submit(fetch_feed, f): f for f in FEEDS}
        for future in concurrent.futures.as_completed(futures):
            f = futures[future]
            got, ok, err = future.result()
            articles.extend(got)
            status.append({"label": f["label"], "ok": ok, "count": len(got), "error": err})
    status.sort(key=lambda s: s["label"])
    return articles, status


# --- seen index --------------------------------------------------------------
def normalize_url(url):
    """Strip tracking noise so the same story is not re-archived per campaign."""
    from urllib.parse import urlsplit, parse_qsl, urlencode, urlunsplit
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    keep = [(k, v) for k, v in parse_qsl(parts.query)
            if not k.lower().startswith(("utm_", "ref", "fbclid", "gclid", "mc_"))]
    path = parts.path.rstrip("/") or "/"
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return urlunsplit((parts.scheme.lower(), host, path, urlencode(keep), ""))


def load_seen(path=SEEN_PATH):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data.get("urls", {})
    except (json.JSONDecodeError, OSError):
        return {}


def save_seen(urls, path=SEEN_PATH):
    cutoff = (datetime.now(JST) - timedelta(days=SEEN_RETENTION_DAYS)).strftime("%Y-%m-%d")
    pruned = {u: d for u, d in urls.items() if d >= cutoff}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"urls": pruned}, f, ensure_ascii=False, indent=0, sort_keys=True)
    os.replace(tmp, path)
    return len(urls) - len(pruned)


# --- scoring -----------------------------------------------------------------
PRIMARY_BONUS = 1.5
CONCRETE_BONUS = 0.35     # per model/benchmark entity, capped


def score_cluster(arts, ents):
    domains = {a["domain"] for a in arts}
    score = 2.0 * (len(domains) - 1)
    score += 0.5 * (len(arts) - 1)
    score += max(a["weight"] for a in arts)
    score += 0.4 * min(gd.ai_relevance(arts), 4)

    # A lone arXiv paper or lab post is never "corroborated by 3 outlets", so
    # without this the sonnet budget goes entirely to syndicated PR.
    if any(a["primary"] for a in arts):
        score += PRIMARY_BONUS

    # Concrete nouns (a named model, a named benchmark) archive better than
    # think-pieces: they are what makes a note findable years later.
    concrete = sum(1 for e in ents if e["type"] in ("model", "benchmark"))
    score += CONCRETE_BONUS * min(concrete, 3)

    newest = max((a["published"] for a in arts if a.get("published")), default=None)
    if newest:
        age_h = (datetime.now(timezone.utc) - newest).total_seconds() / 3600
        if age_h <= 12:
            score += 0.6
        elif age_h <= 24:
            score += 0.3
    return round(score, 3)


def build_clusters(articles, seen):
    usable = []
    skipped_seen = 0
    for a in articles:
        if gd.is_junk(a["title"]):
            continue
        if normalize_url(a["link"]) in seen:
            skipped_seen += 1
            continue
        usable.append(a)

    candidates = []
    dropped_offtopic = 0
    for cluster in gd.cluster_articles(usable):
        arts = sorted(cluster["articles"], key=lambda a: -a["weight"])
        ents = ent.extract_from_articles(arts)

        # Relevance gate. General-tech feeds (The Verge, HN front page) carry a
        # lot of non-AI noise - console ornaments, monitor deals, subway-signal
        # history. On a light day the ranked tail scrapes into that, diluting an
        # archive that is supposed to be *AI* information. A cluster survives
        # only with some AI signal: a known entity, an AI term, or an
        # AI/research feed of origin. Kept deliberately loose so a genuine AI
        # story with no dictionary hit yet still gets through.
        has_signal = (
            bool(ents)
            or gd.ai_relevance(arts) >= 1
            or any(a["category"] in ("ai", "research") for a in arts)
        )
        if not has_signal:
            dropped_offtopic += 1
            continue

        rep = arts[0]
        newest = max((a["published"] for a in arts if a.get("published")), default=None)
        candidates.append({
            "representative_title": rep["title"],
            "representative_url": rep["link"],
            "category": rep["category"],
            "score": score_cluster(arts, ents),
            "entities": ents,
            "domains": sorted({a["domain"] for a in arts}),
            "corroborated": len({a["domain"] for a in arts}) >= 2,
            "published": newest.isoformat() if newest else None,
            "articles": [{
                "title": a["title"],
                "summary": a["summary"],
                "link": a["link"],
                "domain": a["domain"],
                "source_label": a["source_label"],
                "primary": a["primary"],
            } for a in arts],
        })
    candidates.sort(key=lambda c: -c["score"])
    return candidates, skipped_seen, dropped_offtopic


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    ap.add_argument("--tier-a", type=int, default=TIER_A_COUNT)
    ap.add_argument("--out", default=ITEMS_PATH)
    args = ap.parse_args()

    articles, status = collect_all()
    ok_feeds = sum(1 for s in status if s["ok"])
    print("[collect] {}/{} feeds ok, {} articles".format(ok_feeds, len(status), len(articles)))
    for s in status:
        if not s["ok"]:
            print("[collect]   FAILED {}: {}".format(s["label"], s["error"]))

    seen = load_seen()
    candidates, skipped, offtopic = build_clusters(articles, seen)
    print("[collect] {} new clusters ({} articles already archived, {} off-topic dropped)".format(
        len(candidates), skipped, offtopic))

    selected = candidates[:args.limit]
    for i, c in enumerate(selected, 1):
        c["index"] = i
        c["tier"] = "A" if i <= args.tier_a else "B"

    state = {
        "generated_at": datetime.now(JST).isoformat(),
        "date": datetime.now(JST).strftime("%Y-%m-%d"),
        "run_id": datetime.now(JST).strftime("%Y-%m-%d-%H%M"),
        "vocabulary": TAGS,
        "feed_status": status,
        "selected": selected,
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

    tier_a = sum(1 for c in selected if c["tier"] == "A")
    print("[collect] selected {} (tier A {} / tier B {}) -> {}".format(
        len(selected), tier_a, len(selected) - tier_a, args.out))
    if not selected:
        print("[collect] nothing new. Skip the summarize and publish stages.")


if __name__ == "__main__":
    main()
