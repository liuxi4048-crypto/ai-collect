"""
Stage 3 of the *dev-collect* routine: render notes into the Obsidian vault.

Deliberately lighter than publish.py. ai-collect builds an entity graph
(org/model/tech nodes) because its value is the web of relationships between AI
players. A build-reference archive wants something plainer: findable dated
notes grouped by month, faceted by build-domain and tag. So there is no entity
extraction and no canvas here - just topic notes, per-run indexes, and a
dashboard.

Layout (all under `12_Dev Archive/`):

    Dev Archive.md              dashboard / landing page
    Runs/<run_id>.md            per-run index with the theme synthesis
    Topics/<N_分野>/<slug>.md   one note per cluster, foldered by category

Design properties (same guarantees as publish.py):

* Idempotent - existing topic files are skipped; dashboard and run index are
  regenerated from the accumulated index each run.
* Scoped - only `12_Dev Archive/` is ever written or staged. ai-collect's
  `11_AI Archive/` and the hand-maintained vault areas are never touched.
"""
import argparse
import json
import os
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from slugify import make_filename
from dev_vocab import filter_tags
from collect import load_seen, save_seen, normalize_url

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(REPO_ROOT, "data")
JST = timezone(timedelta(hours=9))

VAULT = os.environ.get("OBSIDIAN_VAULT", r"C:\Users\PC_User\ObsidianVault")
ARCHIVE_DIRNAME = "12_Dev Archive"
ARCHIVE_ROOT = os.path.join(VAULT, ARCHIVE_DIRNAME)

TOPICS_DIR = "Topics"
RUNS_DIR = "Runs"
DASHBOARD_NAME = "Dev Archive.md"

ITEMS_PATH = os.path.join(DATA_DIR, "pending_dev_items.json")
NOTES_PATH = os.path.join(DATA_DIR, "pending_dev_notes.json")
INDEX_PATH = os.path.join(DATA_DIR, "dev_index.json")
SEEN_PATH = os.path.join(DATA_DIR, "dev_seen_index.json")

DASHBOARD_RECENT_RUNS = 15
DASHBOARD_RECENT_NOTES = 40

CATEGORY_LABELS = {
    "web": "Web/フロント", "backend": "バックエンド", "language": "言語/ランタイム",
    "cloud": "クラウド", "devops": "DevOps", "data": "データ/DB",
    "security": "セキュリティ", "mobile": "モバイル", "ai-dev": "AI開発",
    "general": "総合",
}

# Topic notes are foldered by build-domain, not by date (user decision
# 2026-07-23: date folders were unreadable). Folder names mirror the
# 11_AI Archive/Topics naming style. Unknown categories fall back to 99_総合.
CATEGORY_DIRS = {
    "web": "1_Web・フロントエンド",
    "backend": "2_バックエンド・API",
    "language": "3_言語・ランタイム",
    "mobile": "4_モバイル",
    "ai-dev": "5_AI開発",
    "cloud": "6_クラウド・インフラ",
    "infra": "6_クラウド・インフラ",
    "devops": "7_DevOps・ツール",
    "tooling": "7_DevOps・ツール",
    "data": "8_データ・DB",
    "security": "9_セキュリティ",
    "general": "99_総合",
}
DEFAULT_CATEGORY_DIR = "99_総合"


def category_dir(cat):
    return CATEGORY_DIRS.get(cat, DEFAULT_CATEGORY_DIR)


# --- helpers -----------------------------------------------------------------
def yaml_str(s):
    return '"{}"'.format(str(s).replace("\\", "\\\\").replace('"', '\\"'))


def safe_join(root, *parts):
    path = os.path.abspath(os.path.join(root, *parts))
    if os.path.commonpath([os.path.abspath(root), path]) != os.path.abspath(root):
        raise ValueError("Path escapes archive root: {!r}".format(path))
    return path


def load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default


def write_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, path)


def write_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def cat_label(cat):
    return CATEGORY_LABELS.get(cat, cat)


# --- topic notes -------------------------------------------------------------
def topic_rel_path(category, filename):
    return "{}/{}/{}".format(TOPICS_DIR, category_dir(category), filename)


def render_topic_note(item, note, run_id, date):
    tags = filter_tags(note.get("tags"))
    category = note.get("category") or item.get("category") or "general"
    arts = item.get("articles", [])

    fm = ["---"]
    fm.append("title: {}".format(yaml_str(note["title_ja"])))
    fm.append("date: {}".format(date))
    fm.append("run_id: {}".format(run_id))
    fm.append("type: dev-archive")
    fm.append("tier: {}".format(item["tier"]))
    fm.append("category: {}".format(category))
    fm.append("corroborated: {}".format("true" if item.get("corroborated") else "false"))
    fm.append("original_title: {}".format(yaml_str(item["representative_title"])))
    fm.append("url: {}".format(yaml_str(item["representative_url"])))
    fm.append("tags:")
    fm.append("  - dev-archive")
    fm.append("  - dev/{}".format(category))
    for t in tags:
        fm.append("  - {}".format(t))
    if arts:
        fm.append("sources:")
        for a in arts:
            fm.append("  - label: {}".format(yaml_str(a["source_label"])))
            fm.append("    url: {}".format(yaml_str(a["link"])))
    fm.append("---")

    body = ["", "# {}".format(note["title_ja"]), ""]
    body.append(note["summary_ja"].strip())
    body.append("")

    label = "裏取り {}ソース".format(len(arts)) if item.get("corroborated") else "単一ソース"
    body.append("> [!quote]- 原文（{}）".format(label))
    for a in arts:
        body.append("> - [{}]({}) — `{}` / {}".format(
            a["title"].replace("]", "］"), a["link"], a["domain"], a["source_label"]))
    body.append("")
    body.append("↩ [[{}]]".format(run_id))
    body.append("")
    return "\n".join(fm + body)


# --- index -------------------------------------------------------------------
def empty_index():
    return {"notes": {}}


def index_note(index, rel_path, meta):
    if rel_path in index["notes"]:
        return False
    index["notes"][rel_path] = meta
    return True


# --- run index ---------------------------------------------------------------
def render_run_note(run_id, date, written, themes, feed_status):
    fm = ["---"]
    fm.append("title: {}".format(yaml_str("dev-collect 実行 {}".format(run_id))))
    fm.append("date: {}".format(date))
    fm.append("run_id: {}".format(run_id))
    fm.append("type: dev-archive-run")
    fm.append("note_count: {}".format(len(written)))
    fm.append("tags:")
    fm.append("  - dev-archive")
    fm.append("  - dev/run")
    fm.append("---")

    body = ["", "# dev-collect 実行 {}".format(run_id), ""]
    body.append("新規ノート **{}件**。".format(len(written)))
    body.append("")

    if themes:
        body.append("## 潮流")
        for t in themes:
            idxs = t.get("note_indexes", [])
            links = " ".join("[[{}]]".format(written[i]["basename"])
                             for i in idxs if i in written)
            body.append("### {}".format(t.get("name", "")))
            body.append(t.get("description", "").strip())
            if links:
                body.append("")
                body.append("→ {}".format(links))
            body.append("")

    body.append("## ノート一覧")
    by_cat = {}
    for i in sorted(written):
        rec = written[i]
        by_cat.setdefault(rec["category"], []).append(rec)
    for cat in sorted(by_cat):
        body.append("")
        body.append("**{}**".format(cat_label(cat)))
        for rec in by_cat[cat]:
            tagstr = " ".join("#{}".format(t) for t in rec["tags"])
            body.append("- [[{}|{}]] {}".format(rec["basename"], rec["title_ja"], tagstr).rstrip())
    body.append("")

    failed = [s for s in feed_status if not s.get("ok")]
    if failed:
        body.append("> [!warning]- 取得失敗フィード ({})".format(len(failed)))
        for s in failed:
            body.append("> - {}: {}".format(s["label"], s.get("error", "")))
        body.append("")
    return "\n".join(fm + body)


# --- dashboard ---------------------------------------------------------------
def render_dashboard(index):
    notes = index["notes"]
    total = len(notes)
    cat_counts = Counter(n["category"] for n in notes.values())
    tag_counts = Counter(t for n in notes.values() for t in n.get("tags", []))

    recent = sorted(notes.items(), key=lambda kv: kv[1]["date"], reverse=True)

    runs = {}
    for n in notes.values():
        runs.setdefault(n["run_id"], 0)
        runs[n["run_id"]] += 1
    recent_runs = sorted(runs.items(), reverse=True)[:DASHBOARD_RECENT_RUNS]

    out = ["---", "title: Dev Archive", "type: dev-archive-dashboard",
           "tags:", "  - dev-archive", "---", "",
           "# 🛠 Dev Archive", "",
           "システム/アプリ開発の参考情報アーカイブ。`/dev-collect` が無差別収集して蓄積。",
           "AIニュース特化の [[AI Archive]] とは別系統。", "",
           "**総ノート数: {}**".format(total), ""]

    out.append("## 分野別")
    out.append("ノートは `Topics/` 配下の分野フォルダに置かれる。")
    for cat, c in sorted(cat_counts.items(), key=lambda kv: -kv[1]):
        out.append("- **{}** — {}件 (`Topics/{}`)".format(cat_label(cat), c, category_dir(cat)))
    out.append("")

    if tag_counts:
        out.append("## タグ")
        facets = ["#{} ({})".format(t, c) for t, c in tag_counts.most_common(30)]
        out.append(" · ".join(facets))
        out.append("")

    out.append("## 直近の実行")
    for run_id, c in recent_runs:
        out.append("- [[{}]] — {}件".format(run_id, c))
    out.append("")

    out.append("## 直近のノート")
    for _rel, n in recent[:DASHBOARD_RECENT_NOTES]:
        out.append("- {} [[{}|{}]] `{}`".format(
            n["date"], n["basename"], n["title_ja"], cat_label(n["category"])))
    out.append("")
    return "\n".join(out)


# --- git ---------------------------------------------------------------------
def git(*args, cwd=VAULT, check=True):
    proc = subprocess.run(["git", "-C", cwd] + list(args),
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and proc.returncode != 0:
        raise RuntimeError("git {} failed: {}".format(" ".join(args), proc.stderr.strip()))
    return proc


def commit_and_push(message, push=True):
    git("add", "--", ARCHIVE_DIRNAME)
    staged = git("diff", "--cached", "--quiet", "--", ARCHIVE_DIRNAME, check=False)
    if staged.returncode == 0:
        print("[dev-publish] nothing to commit")
        return False
    git("commit", "-q", "-m", message)
    if push:
        git("push", "-q")
        print("[dev-publish] pushed")
    else:
        print("[dev-publish] committed (push skipped)")
    return True


# --- main --------------------------------------------------------------------
def do_publish(args):
    state = load_json(args.items, None)
    if not state:
        raise SystemExit("No collected state at {}. Run the collect stage first.".format(args.items))
    payload = load_json(args.notes, None)
    if payload is None:
        raise SystemExit("No summaries at {}.".format(args.notes))

    notes_in = payload.get("notes", payload if isinstance(payload, list) else [])
    themes = payload.get("themes", [])
    by_index = {n["index"]: n for n in notes_in if isinstance(n, dict) and "index" in n}

    run_id = state["run_id"]
    date = state["date"]
    index = load_json(INDEX_PATH, empty_index())
    index.setdefault("notes", {})
    seen = load_seen(SEEN_PATH)

    written, missing, skipped_existing = {}, [], 0

    for item in state["selected"]:
        idx = item["index"]
        note = by_index.get(idx)
        if not note or not note.get("title_ja") or not note.get("summary_ja"):
            missing.append(idx)
            continue

        tags = filter_tags(note.get("tags"))
        category = note.get("category") or item.get("category") or "general"
        filename = make_filename(date, item["representative_title"], item["representative_url"])
        rel_path = topic_rel_path(category, filename)
        abs_path = safe_join(ARCHIVE_ROOT, TOPICS_DIR, category_dir(category), filename)

        if os.path.exists(abs_path):
            skipped_existing += 1
        else:
            write_text(abs_path, render_topic_note(item, note, run_id, date))

        # Recorded only after the file exists, so a crash mid-run leaves the
        # remaining items collectable rather than silently marked done.
        for a in item["articles"]:
            seen[normalize_url(a["link"])] = date

        basename = filename[:-3]  # strip .md for wikilinks
        meta = {
            "date": date, "run_id": run_id, "title_ja": note["title_ja"],
            "category": category, "tags": tags, "basename": basename,
            "url": item["representative_url"],
        }
        index_note(index, rel_path, meta)
        written[idx] = meta

    # Run index + dashboard, regenerated from the accumulated index each run.
    write_text(safe_join(ARCHIVE_ROOT, RUNS_DIR, run_id + ".md"),
               render_run_note(run_id, date, written, themes, state.get("feed_status", [])))
    write_text(safe_join(ARCHIVE_ROOT, DASHBOARD_NAME), render_dashboard(index))

    write_json(INDEX_PATH, index)
    save_seen(seen, SEEN_PATH)

    print("[dev-publish] {} written, {} skipped (existing), {} missing summaries".format(
        len(written), skipped_existing, len(missing)))
    if missing:
        print("[dev-publish]   missing indexes: {}".format(missing))

    if not args.no_commit:
        commit_and_push("dev-collect: archive run {}".format(run_id), push=not args.no_push)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--items", default=ITEMS_PATH)
    ap.add_argument("--notes", default=NOTES_PATH)
    ap.add_argument("--no-push", action="store_true")
    ap.add_argument("--no-commit", action="store_true")
    args = ap.parse_args()
    if args.no_commit:
        args.no_push = True
    do_publish(args)


if __name__ == "__main__":
    main()
