"""
Weekly trend report: read data/entity_index.json, write one report note into
`10_情報/AI Archive/Reports/`.

Pure aggregation - no network, no LLM. The note contains computed statistics
plus empty `AI解説` callouts that Claude fills in afterwards (same division of
labour as collect/publish: the script gathers, Claude writes).

Metrics, all computed from the index alone:

* mention counts per entity, this period vs the previous same-length period
* entities whose `first_seen` falls inside the period
* co-occurrence pairs whose first shared note falls inside the period
* topic counts per tag group

Usage:
    python trends.py                 # report for the 7 days ending today (JST)
    python trends.py --days 7 --end 2026-07-27
    python trends.py --commit        # also git add/commit/push the archive
"""
import argparse
import os
import subprocess
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from itertools import combinations

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from vocab import TAG_GROUPS

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX_PATH = os.path.join(REPO_ROOT, "data", "entity_index.json")
JST = timezone(timedelta(hours=9))

VAULT = os.environ.get("OBSIDIAN_VAULT", r"C:\Users\PC_User\ObsidianVault")
ARCHIVE_DIRNAME = "10_情報/AI Archive"
REPORTS_DIR = os.path.join(VAULT, ARCHIVE_DIRNAME, "Reports")

MOVER_LIMIT = 10
NEW_PAIR_LIMIT = 12
FRESH_LIMIT = 15
MIN_MENTIONS = 2   # below this a "rise" is noise


def load_index():
    import json
    with open(INDEX_PATH, encoding="utf-8") as f:
        return json.load(f)


def yaml_str(s):
    return '"{}"'.format(str(s).replace("\\", "\\\\").replace('"', '\\"'))


def note_dates(index):
    """path -> date string for every topic note."""
    return {path: m["date"] for path, m in index["notes"].items()}


def mentions_in(index, lo, hi):
    """entity -> number of notes mentioning it with lo <= date <= hi."""
    out = Counter()
    for name, e in index["entities"].items():
        for n in e.get("notes", []):
            if lo <= n["date"] <= hi:
                out[name] += 1
    return out


def tag_counts_in(index, lo, hi):
    out = Counter()
    for m in index["notes"].values():
        if lo <= m["date"] <= hi:
            for t in m.get("tags", []):
                out[t] += 1
    return out


def pair_first_dates(index):
    """(a, b) -> date of the first note both entities share."""
    by_note = {}
    for name, e in index["entities"].items():
        for n in e.get("notes", []):
            by_note.setdefault(n["path"], set()).add(name)
    dates = note_dates(index)
    first = {}
    for path, names in by_note.items():
        d = dates.get(path)
        if not d or len(names) < 2:
            continue
        for a, b in combinations(sorted(names), 2):
            if (a, b) not in first or d < first[(a, b)]:
                first[(a, b)] = d
    return first


def build_report(index, lo, hi, prev_lo, prev_hi):
    cur = mentions_in(index, lo, hi)
    prev = mentions_in(index, prev_lo, prev_hi)

    movers = []
    for name, c in cur.items():
        p = prev.get(name, 0)
        if c >= MIN_MENTIONS and c > p:
            movers.append((c - p, c, p, name))
    movers.sort(reverse=True)

    # Most-mentioned first: on a cold start every entity is "new", and the
    # interesting ones are the busy ones, not the alphabetical head.
    fresh = sorted(
        ((e["first_seen"], name, cur.get(name, 0))
         for name, e in index["entities"].items()
         if lo <= e.get("first_seen", "") <= hi),
        key=lambda t: (-t[2], t[0], t[1]))

    ents = index["entities"]
    new_pairs = sorted(
        ((d, a, b) for (a, b), d in pair_first_dates(index).items()
         if lo <= d <= hi
         and ents[a].get("first_seen", "") < lo
         and ents[b].get("first_seen", "") < lo),
        reverse=True)[:NEW_PAIR_LIMIT]

    cur_tags = tag_counts_in(index, lo, hi)
    prev_tags = tag_counts_in(index, prev_lo, prev_hi)
    cur_total = sum(1 for m in index["notes"].values() if lo <= m["date"] <= hi)
    prev_total = sum(1 for m in index["notes"].values() if prev_lo <= m["date"] <= prev_hi)

    iso = date.fromisoformat(hi).isocalendar()
    week_id = "{}-W{:02d}".format(iso[0], iso[1])
    title = "週次AIトレンド {}".format(week_id)

    fm = ["---",
          "title: {}".format(yaml_str(title)),
          "type: ai-trend-report",
          "week: {}".format(week_id),
          "period_start: {}".format(lo),
          "period_end: {}".format(hi),
          "topic_count: {}".format(cur_total),
          "generated: {}".format(datetime.now(JST).strftime("%Y-%m-%d %H:%M")),
          "tags:", "  - ai-archive", "  - trend-report",
          "---", ""]

    b = ["# 📊 {}".format(title), "",
         "> [!abstract] 対象期間 `{}` 〜 `{}`".format(lo, hi),
         "> トピック **{}件**（前期間 {}件）。[[AI Archive]] の蓄積から自動集計。".format(
             cur_total, prev_total),
         ""]

    b.append("> [!note] AI解説 — 今週の総括")
    b.append("> _（Claudeがここに3〜5行で今週の潮流を書く）_")
    b.append("")

    b.append("## 📈 言及が伸びたエンティティ")
    b.append("")
    if movers:
        b.append("| エンティティ | 今期 | 前期 | 増分 |")
        b.append("|---|---|---|---|")
        for diff, c, p, name in movers[:MOVER_LIMIT]:
            b.append("| [[{}]] | {} | {} | +{} |".format(name, c, p, diff))
    else:
        b.append("_該当なし（データ蓄積待ち）_")
    b.append("")

    b.append("## ✨ 初登場エンティティ")
    b.append("")
    if fresh:
        for d, name, c in fresh[:FRESH_LIMIT]:
            b.append("- `{}` [[{}]] — 期間内 {} 件".format(d, name, c))
        if len(fresh) > FRESH_LIMIT:
            b.append("- _…他 {} 件_".format(len(fresh) - FRESH_LIMIT))
    else:
        b.append("_この期間の新出現なし_")
    b.append("")

    b.append("## 🔗 新しく結びついたペア")
    b.append("")
    b.append("既知同士のエンティティが、この期間に初めて同じトピックで語られた組み合わせ。")
    b.append("")
    if new_pairs:
        for d, a, bname in new_pairs:
            b.append("- `{}` [[{}]] × [[{}]]".format(d, a, bname))
    else:
        b.append("_該当なし_")
    b.append("")

    b.append("> [!note] AI解説 — 注目の動き")
    b.append("> _（Claudeがここに、上の表から特に意味のある動き2〜3個の背景を書く）_")
    b.append("")

    b.append("## 🏷 カテゴリ別トピック数")
    b.append("")
    b.append("| カテゴリ | 今期 | 前期 |")
    b.append("|---|---|---|")
    for group_name, tags in TAG_GROUPS:
        c = sum(cur_tags.get(t, 0) for t in tags)
        p = sum(prev_tags.get(t, 0) for t in tags)
        b.append("| {} | {} | {} |".format(group_name, c, p))
    b.append("")

    b.append("---")
    b.append("元データ: `ai-collect` の `entity_index.json`（`trends.py` が自動集計）")
    b.append("")
    return week_id, "\n".join(fm + b)


def git(*args, check=True):
    return subprocess.run(["git", "-C", VAULT] + list(args),
                          capture_output=True, text=True, check=check)


def commit_and_push(message):
    git("add", "--", ARCHIVE_DIRNAME)
    if git("diff", "--cached", "--quiet", "--", ARCHIVE_DIRNAME, check=False).returncode == 0:
        print("[trends] nothing to commit")
        return
    git("commit", "-q", "-m", message)
    git("push", "-q")
    print("[trends] pushed")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--end", help="period end date YYYY-MM-DD (default: today JST)")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--commit", action="store_true",
                    help="git add/commit/push the archive after writing")
    ap.add_argument("--force", action="store_true",
                    help="overwrite an existing report (discards Claude's narrative)")
    args = ap.parse_args()

    hi_d = date.fromisoformat(args.end) if args.end else datetime.now(JST).date()
    lo_d = hi_d - timedelta(days=args.days - 1)
    prev_hi = lo_d - timedelta(days=1)
    prev_lo = prev_hi - timedelta(days=args.days - 1)

    index = load_index()
    week_id, text = build_report(index, lo_d.isoformat(), hi_d.isoformat(),
                                 prev_lo.isoformat(), prev_hi.isoformat())

    os.makedirs(REPORTS_DIR, exist_ok=True)
    # Single rolling note — no date-named files (2026-07-23 user decision).
    # The week id lives inside the note content, not in the filename.
    out = os.path.join(REPORTS_DIR, "トレンドレポート.md")
    if os.path.exists(out) and not args.force:
        # The existing file may hold hand-written narrative; never clobber it
        # on a re-run (the scheduled task is weekly, so this is the rare case).
        print("[trends] exists, skipped (use --force to overwrite): {}".format(out))
    else:
        with open(out, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        print("[trends] wrote {}".format(out))

    if args.commit:
        commit_and_push("ai-collect: weekly trend report {}".format(week_id))


if __name__ == "__main__":
    main()
