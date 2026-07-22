"""
Stage 1 of the *dev-collect* routine: gather build-relevant items broadly.

This is the AI-collect pipeline with one deliberate change: the AI-relevance
gate is removed. ai-collect drops any cluster that carries no AI signal; that
gate is exactly what we must NOT apply here, because a Postgres release or a
Vite RC is build-relevant while mentioning zero AI keywords. "Indiscriminate"
is the requirement, so the only filters left are junk-title and already-seen.

Everything else is reused from collect.py (fetch, clustering, seen index, URL
normalization) so the two routines stay behaviourally consistent and there is
one place to fix a fetch or clustering bug. Reads DEV_FEEDS, writes its own
pending / seen files - it never touches ai-collect's state.
"""
import argparse
import concurrent.futures
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import collect  # reuse fetch_feed, gd, load_seen, save_seen, normalize_url
from dev_feeds import DEV_FEEDS
from dev_vocab import DEV_TAGS

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(REPO_ROOT, "data")

SEEN_PATH = os.path.join(DATA_DIR, "dev_seen_index.json")
ITEMS_PATH = os.path.join(DATA_DIR, "pending_dev_items.json")

DEFAULT_LIMIT = 60
TIER_A_COUNT = 12
FETCH_WORKERS = 8

PRIMARY_BONUS = 1.5


def collect_all():
    """Fetch every DEV feed concurrently. Mirrors collect.collect_all but over
    DEV_FEEDS; fetch_feed itself is shared so failures are handled identically."""
    articles, status = [], []
    with concurrent.futures.ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
        futures = {pool.submit(collect.fetch_feed, f): f for f in DEV_FEEDS}
        for future in concurrent.futures.as_completed(futures):
            f = futures[future]
            got, ok, err = future.result()
            articles.extend(got)
            status.append({"label": f["label"], "ok": ok, "count": len(got), "error": err})
    status.sort(key=lambda s: s["label"])
    return articles, status


def score_cluster(arts):
    """No entity graph here, so the score is corroboration + source weight +
    freshness. Kept intentionally close to collect.score_cluster minus the
    AI-specific terms."""
    domains = {a["domain"] for a in arts}
    score = 2.0 * (len(domains) - 1)
    score += 0.5 * (len(arts) - 1)
    score += max(a["weight"] for a in arts)
    if any(a["primary"] for a in arts):
        score += PRIMARY_BONUS
    newest = max((a["published"] for a in arts if a.get("published")), default=None)
    if newest:
        age_h = (datetime.now(timezone.utc) - newest).total_seconds() / 3600
        if age_h <= 24:
            score += 0.6
        elif age_h <= 48:
            score += 0.3
    return round(score, 3)


def build_clusters(articles, seen):
    """Cluster and rank. Unlike collect.build_clusters there is NO relevance
    gate - only junk-title and seen filtering. That absence is the whole point
    of this routine."""
    usable = []
    skipped_seen = 0
    for a in articles:
        if collect.gd.is_junk(a["title"]):
            continue
        if collect.normalize_url(a["link"]) in seen:
            skipped_seen += 1
            continue
        usable.append(a)

    candidates = []
    for cluster in collect.gd.cluster_articles(usable):
        arts = sorted(cluster["articles"], key=lambda a: -a["weight"])
        rep = arts[0]
        newest = max((a["published"] for a in arts if a.get("published")), default=None)
        candidates.append({
            "representative_title": rep["title"],
            "representative_url": rep["link"],
            "category": rep["category"],
            "score": score_cluster(arts),
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
    return candidates, skipped_seen


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    ap.add_argument("--tier-a", type=int, default=TIER_A_COUNT)
    ap.add_argument("--out", default=ITEMS_PATH)
    args = ap.parse_args()

    articles, status = collect_all()
    ok_feeds = sum(1 for s in status if s["ok"])
    print("[dev-collect] {}/{} feeds ok, {} articles".format(ok_feeds, len(status), len(articles)))
    for s in status:
        if not s["ok"]:
            print("[dev-collect]   FAILED {}: {}".format(s["label"], s["error"]))

    seen = collect.load_seen(SEEN_PATH)
    candidates, skipped = build_clusters(articles, seen)
    print("[dev-collect] {} new clusters ({} already archived)".format(len(candidates), skipped))

    selected = candidates[:args.limit]
    for i, c in enumerate(selected, 1):
        c["index"] = i
        c["tier"] = "A" if i <= args.tier_a else "B"

    state = {
        "generated_at": datetime.now(collect.JST).isoformat(),
        "date": datetime.now(collect.JST).strftime("%Y-%m-%d"),
        "run_id": datetime.now(collect.JST).strftime("%Y-%m-%d-%H%M"),
        "vocabulary": DEV_TAGS,
        "feed_status": status,
        "selected": selected,
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

    tier_a = sum(1 for c in selected if c["tier"] == "A")
    print("[dev-collect] selected {} (tier A {} / tier B {}) -> {}".format(
        len(selected), tier_a, len(selected) - tier_a, args.out))
    if not selected:
        print("[dev-collect] nothing new. Skip the summarize and publish stages.")


if __name__ == "__main__":
    main()
