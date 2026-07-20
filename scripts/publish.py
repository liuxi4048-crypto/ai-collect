"""
Stage 3: render notes into the Obsidian vault, then commit.

Two properties matter more than anything else here:

* **Idempotent.** Re-running after a crash must not duplicate notes or
  double-count co-occurrences. Existing note files are skipped, and the index
  records which note paths it has already absorbed.
* **Scoped.** Only `11_AI Archive/` is ever written or staged. The vault also
  holds a hand-maintained area and another generator's output; neither may be
  touched by an unattended run.
"""
import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import entities as ent
from slugify import make_filename, entity_filename
from vocab import TAG_GROUPS, filter_tags
from collect import load_seen, save_seen, normalize_url

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(REPO_ROOT, "data")
JST = timezone(timedelta(hours=9))

VAULT = os.environ.get("OBSIDIAN_VAULT", r"C:\Users\PC_User\ObsidianVault")
ARCHIVE_DIRNAME = "11_AI Archive"
ARCHIVE_ROOT = os.path.join(VAULT, ARCHIVE_DIRNAME)

ITEMS_PATH = os.path.join(DATA_DIR, "pending_items.json")
NOTES_PATH = os.path.join(DATA_DIR, "pending_notes.json")
INDEX_PATH = os.path.join(DATA_DIR, "entity_index.json")
CANDIDATES_PATH = os.path.join(DATA_DIR, "alias_candidates.json")

ENTITY_NOTE_MIN_NOTES = 3     # below this, the node is noise in the graph
MOC_PER_TAG = 25
TYPE_LABELS = {"org": "組織", "model": "モデル", "tech": "技術", "benchmark": "ベンチマーク"}


# --- helpers -----------------------------------------------------------------
def yaml_str(s):
    return '"{}"'.format(str(s).replace("\\", "\\\\").replace('"', '\\"'))


def safe_join(root, *parts):
    """Join and prove the result stayed under root.

    Filenames come from slugify, which cannot emit a separator - this is the
    second line of defence, not the first.
    """
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
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


# --- topic notes -------------------------------------------------------------
def render_topic_note(item, note, run_id):
    date = item.get("date") or run_id[:10]
    tags = filter_tags(note.get("tags"))
    ents = item.get("entities", [])
    arts = item.get("articles", [])

    fm = ["---"]
    fm.append("title: {}".format(yaml_str(note["title_ja"])))
    fm.append("date: {}".format(date))
    fm.append("run_id: {}".format(run_id))
    fm.append("type: ai-archive")
    fm.append("tier: {}".format(item["tier"]))
    fm.append("category: {}".format(item["category"]))
    fm.append("domain: {}".format(item.get("domains", ["?"])[0]))
    fm.append("corroborated: {}".format("true" if item.get("corroborated") else "false"))
    fm.append("original_title: {}".format(yaml_str(item["representative_title"])))
    fm.append("url: {}".format(yaml_str(item["representative_url"])))
    if tags:
        fm.append("tags:")
        fm.extend("  - {}".format(t) for t in tags)
    if ents:
        fm.append("entities:")
        fm.extend("  - {}".format(yaml_str(e["name"])) for e in ents)
    if arts:
        fm.append("sources:")
        for a in arts:
            fm.append("  - label: {}".format(yaml_str(a["source_label"])))
            fm.append("    url: {}".format(yaml_str(a["link"])))
    fm.append("---")

    body = ["", "# {}".format(note["title_ja"]), ""]
    body.append(note["summary_ja"].strip())
    body.append("")

    if ents:
        grouped = {}
        for e in ents:
            grouped.setdefault(e["type"], []).append(e["name"])
        body.append("> [!info] 関連")
        for etype in ("org", "model", "tech", "benchmark"):
            if etype in grouped:
                links = " · ".join("[[{}]]".format(n) for n in grouped[etype])
                body.append("> **{}**: {}".format(TYPE_LABELS[etype], links))
        body.append("")

    label = "裏取り {}ソース".format(len(arts)) if item.get("corroborated") else "単一ソース"
    body.append("> [!quote]- 原文（{}）".format(label))
    for a in arts:
        body.append("> - [{}]({}) — `{}` / {}".format(
            a["title"].replace("]", "］"), a["link"], a["domain"], a["source_label"]))
    body.append("")
    body.append("↩ [[{}]]".format(run_note_name(run_id)))
    body.append("")
    return "\n".join(fm + body)


def run_note_name(run_id):
    return run_id


# --- index -------------------------------------------------------------------
def empty_index():
    return {"entities": {}, "notes": {}}


def index_note(index, rel_path, meta):
    """Absorb one note into the index. No-op if already absorbed."""
    if rel_path in index["notes"]:
        return False
    index["notes"][rel_path] = {
        "date": meta["date"],
        "title_ja": meta["title_ja"],
        "tags": meta["tags"],
        "tier": meta["tier"],
        "run_id": meta["run_id"],
    }
    names = [e["name"] for e in meta["entities"]]
    for e in meta["entities"]:
        rec = index["entities"].setdefault(e["name"], {
            "type": e["type"], "first_seen": meta["date"], "notes": [], "cooccur": {},
        })
        rec["type"] = e["type"]
        if meta["date"] < rec["first_seen"]:
            rec["first_seen"] = meta["date"]
        rec["notes"].append({"path": rel_path, "date": meta["date"], "title_ja": meta["title_ja"]})
        for other in names:
            if other != e["name"]:
                rec["cooccur"][other] = rec["cooccur"].get(other, 0) + 1
    return True


def render_entity_note(name, rec):
    notes = sorted(rec["notes"], key=lambda n: n["date"], reverse=True)
    # The canonical name itself must be an alias: reserved-name escaping can
    # make the filename differ from the title, and [[Claude]] has to keep
    # resolving to claude-entity.md.
    aliases = sorted(set(ent.aliases_for(name)) | {name})
    cooccur = sorted(rec["cooccur"].items(), key=lambda kv: (-kv[1], kv[0]))[:5]

    fm = ["---"]
    fm.append("title: {}".format(yaml_str(name)))
    fm.append("type: ai-entity")
    fm.append("entity_type: {}".format(rec["type"]))
    fm.append("first_seen: {}".format(rec["first_seen"]))
    fm.append("note_count: {}".format(len(notes)))
    if aliases:
        fm.append("aliases:")
        fm.extend("  - {}".format(yaml_str(a)) for a in aliases)
    fm.append("tags:")
    fm.append("  - ai-entity")
    fm.append("  - entity/{}".format(rec["type"]))
    fm.append("---")

    body = ["", "# {}".format(name), ""]
    body.append("> [!abstract] {}".format(TYPE_LABELS.get(rec["type"], rec["type"])))
    body.append("> 初出 **{}** — 言及 **{}件**".format(rec["first_seen"], len(notes)))
    body.append("")

    body.append("## 直近のトピック")
    body.append("")
    for n in notes[:3]:
        body.append("- `{}` [[{}|{}]]".format(n["date"], note_link_target(n["path"]), n["title_ja"]))
    body.append("")

    if cooccur:
        body.append("## よく一緒に語られる")
        body.append("")
        # This is the one thing Obsidian's backlinks pane cannot show, and the
        # reason these notes are generated at all.
        for other, count in cooccur:
            body.append("- [[{}]] — {}件".format(other, count))
        body.append("")

    if len(notes) > 3:
        body.append("> [!note]- すべての言及（{}件）".format(len(notes)))
        for n in notes:
            body.append("> - `{}` [[{}|{}]]".format(n["date"], note_link_target(n["path"]), n["title_ja"]))
        body.append("")
    return "\n".join(fm + body)


def note_link_target(rel_path):
    """Obsidian resolves links by basename when unique; ours carry a hash."""
    return os.path.splitext(os.path.basename(rel_path))[0]


# --- run note ----------------------------------------------------------------
def render_run_note(state, written, skipped_existing, missing, trends, candidates_added):
    run_id = state["run_id"]
    counts = Counter(i["category"] for i in state["selected"])
    tier_a = sum(1 for i in state["selected"] if i["tier"] == "A")

    fm = ["---"]
    fm.append("title: {}".format(yaml_str("AI Collect — " + run_id)))
    fm.append("date: {}".format(state["date"]))
    fm.append("run_id: {}".format(run_id))
    fm.append("type: ai-archive-run")
    fm.append("topics: {}".format(len(state["selected"])))
    # `written` counts every note this run is responsible for, including ones a
    # previous attempt already wrote. Only the difference is actually new.
    new_count = len(written) - skipped_existing
    fm.append("new_notes: {}".format(new_count))
    fm.append("tags:")
    fm.append("  - ai-archive")
    fm.append("  - run-index")
    fm.append("---")

    body = ["", "# 🗂 AI Collect — {}".format(run_id), ""]

    if trends:
        body.append("## 📈 この回の潮流")
        body.append("")
        for i, t in enumerate(trends, 1):
            body.append("### {}. {}".format(i, t["name"]))
            body.append("")
            body.append(t["description"].strip())
            body.append("")
            links = [x for x in t.get("note_indexes", []) if x in written]
            if links:
                body.append("関連: " + " · ".join(
                    "[[{}|{}]]".format(note_link_target(written[x]["path"]), written[x]["title_ja"])
                    for x in links))
                body.append("")
    else:
        body.append("> [!warning] 潮流の合成なし")
        body.append("> このrunでは合成トレンドが生成されなかった。")
        body.append("")

    body.append("## 内訳")
    body.append("")
    body.append("- 新規ノート **{}件**（既存スキップ {}件 / この回の対象 {}件）".format(
        new_count, skipped_existing, len(written)))
    body.append("- tier A {} / tier B {}".format(tier_a, len(state["selected"]) - tier_a))
    body.append("- カテゴリ: " + " / ".join("{} {}".format(k, v) for k, v in sorted(counts.items())))
    failed = [s for s in state.get("feed_status", []) if not s["ok"]]
    if failed:
        body.append("- 取得失敗フィード: " + ", ".join(s["label"] for s in failed))
    if missing:
        body.append("- ⚠ 未要約でスキップ: **{}件**（index {}）".format(
            len(missing), ", ".join(str(m) for m in missing[:20])))
    if candidates_added:
        body.append("- 🆕 辞書未登録の新語候補: **{}件**（`data/alias_candidates.json`）".format(candidates_added))
    body.append("")

    if written:
        body.append("> [!note]- 収集した全ノート（{}件）".format(len(written)))
        for idx in sorted(written):
            w = written[idx]
            body.append("> - `{}` [[{}|{}]]".format(w["tier"], note_link_target(w["path"]), w["title_ja"]))
        body.append("")
    return "\n".join(fm + body)


def render_moc(index):
    notes = index["notes"]
    by_tag = {}
    for path, meta in notes.items():
        for t in meta.get("tags", []):
            by_tag.setdefault(t, []).append((meta["date"], meta["title_ja"], path))

    ents = index["entities"]
    by_type = {}
    for name, rec in ents.items():
        if len(rec["notes"]) >= ENTITY_NOTE_MIN_NOTES:
            by_type.setdefault(rec["type"], []).append((len(rec["notes"]), name))

    out = ["---",
           "title: {}".format(yaml_str("AI Archive MOC")),
           "type: ai-archive-moc",
           "note_count: {}".format(len(notes)),
           "tags:", "  - ai-archive", "  - moc",
           "---", "",
           "# 🗺 AI Archive MOC", "",
           "アーカイブ **{}ノート** / エンティティ **{}件**".format(len(notes), len(ents)), "",
           "> [!tip] 使い方",
           "> グラフビューで `entity/tech` タグのノードを辿ると、別々の組織が同じ技術で",
           "> つながっているところが見える。日付で追うより発見が多い。", ""]

    out.append("## エンティティ")
    out.append("")
    for etype in ("org", "model", "tech", "benchmark"):
        items = sorted(by_type.get(etype, []), reverse=True)
        if not items:
            continue
        out.append("**{}**: ".format(TYPE_LABELS[etype]) + " · ".join(
            "[[{}]] ({})".format(name, count) for count, name in items[:20]))
        out.append("")

    out.append("## トピック")
    out.append("")
    for group_name, tags in TAG_GROUPS:
        present = [t for t in tags if by_tag.get(t)]
        if not present:
            continue
        out.append("### {}".format(group_name))
        out.append("")
        for t in present:
            rows = sorted(by_tag[t], reverse=True)
            out.append("> [!note]- #{} — {}件".format(t, len(rows)))
            for date, title, path in rows[:MOC_PER_TAG]:
                out.append("> - `{}` [[{}|{}]]".format(date, note_link_target(path), title))
            out.append("")
    return "\n".join(out) + "\n"


# --- git ---------------------------------------------------------------------
def git(*args, cwd=VAULT, check=True):
    proc = subprocess.run(["git", "-C", cwd] + list(args),
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and proc.returncode != 0:
        raise RuntimeError("git {} failed: {}".format(" ".join(args), proc.stderr.strip()))
    return proc


def commit_and_push(run_id, count, push=True):
    git("add", "--", ARCHIVE_DIRNAME)
    staged = git("diff", "--cached", "--quiet", "--", ARCHIVE_DIRNAME, check=False)
    if staged.returncode == 0:
        print("[publish] nothing to commit")
        return False
    git("commit", "-q", "-m", "ai-collect: {} ({}件)".format(run_id, count))
    if push:
        git("push", "-q")
        print("[publish] pushed")
    else:
        print("[publish] committed (push skipped)")
    return True


# --- main --------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--items", default=ITEMS_PATH)
    ap.add_argument("--notes", default=NOTES_PATH)
    ap.add_argument("--no-push", action="store_true")
    ap.add_argument("--no-commit", action="store_true")
    args = ap.parse_args()

    if not os.path.isdir(VAULT):
        raise SystemExit("Vault not found: {}".format(VAULT))
    state = load_json(args.items, None)
    if not state:
        raise SystemExit("No collected state at {}. Run the collect stage first.".format(args.items))
    payload = load_json(args.notes, None)
    if payload is None:
        raise SystemExit("No summaries at {}.".format(args.notes))

    notes_in = payload.get("notes", payload if isinstance(payload, list) else [])
    trends = payload.get("trends", [])
    new_terms = payload.get("new_terms", [])
    by_index = {n["index"]: n for n in notes_in if isinstance(n, dict) and "index" in n}

    run_id = state["run_id"]
    index = load_json(INDEX_PATH, empty_index())
    index.setdefault("entities", {})
    index.setdefault("notes", {})
    seen = load_seen()

    written, missing, skipped_existing = {}, [], 0

    for item in state["selected"]:
        idx = item["index"]
        note = by_index.get(idx)
        if not note or not note.get("title_ja") or not note.get("summary_ja"):
            missing.append(idx)
            continue

        date = state["date"]
        filename = make_filename(date, item["representative_title"], item["representative_url"])
        rel_path = "{}/{}/{}".format(date[:4], date[5:7], filename)
        abs_path = safe_join(ARCHIVE_ROOT, date[:4], date[5:7], filename)

        if os.path.exists(abs_path):
            skipped_existing += 1
        else:
            item_with_date = dict(item, date=date)
            write_text(abs_path, render_topic_note(item_with_date, note, run_id))

        # Recorded only after the file exists, so a crash mid-run leaves the
        # remaining items collectable rather than silently marked done.
        for a in item["articles"]:
            seen[normalize_url(a["link"])] = date
        save_seen(seen)

        meta = {
            "date": date,
            "title_ja": note["title_ja"],
            "tags": filter_tags(note.get("tags")),
            "tier": item["tier"],
            "run_id": run_id,
            "entities": item.get("entities", []),
        }
        index_note(index, rel_path, meta)
        written[idx] = {"path": rel_path, "title_ja": note["title_ja"], "tier": item["tier"]}

    # entity notes
    entity_written = 0
    for name, rec in index["entities"].items():
        if len(rec["notes"]) < ENTITY_NOTE_MIN_NOTES:
            continue
        path = safe_join(ARCHIVE_ROOT, "_Entities", entity_filename(name))
        write_text(path, render_entity_note(name, rec))
        entity_written += 1

    # new-term candidates: accumulate, never auto-adopt
    candidates_added = 0
    if new_terms:
        cands = load_json(CANDIDATES_PATH, {"candidates": {}})
        cands.setdefault("candidates", {})
        known = set(ent.known_canonicals())
        for t in new_terms:
            if not isinstance(t, dict) or not t.get("term"):
                continue
            key = t["term"].strip().lower()
            if not key or t.get("term") in known:
                continue
            rec = cands["candidates"].setdefault(key, {
                "type": t.get("type", "unknown"), "count": 0, "first_seen": state["date"],
                "example": t.get("context", "")[:200],
            })
            rec["count"] += 1
            rec["last_seen"] = state["date"]
            candidates_added += 1
        write_json(CANDIDATES_PATH, cands)

    run_path = safe_join(ARCHIVE_ROOT, "_Runs", run_id + ".md")
    write_text(run_path, render_run_note(state, written, skipped_existing, missing,
                                         trends, candidates_added))
    write_text(safe_join(ARCHIVE_ROOT, "_AI Archive MOC.md"), render_moc(index))
    write_json(INDEX_PATH, index)

    print("[publish] {} new notes, {} skipped (existing), {} unsummarized".format(
        len(written) - skipped_existing, skipped_existing, len(missing)))
    print("[publish] {} entity notes, {} total archive notes".format(entity_written, len(index["notes"])))
    if missing:
        print("[publish] unsummarized indexes: {}".format(missing))
    if not args.no_commit:
        commit_and_push(run_id, len(written), push=not args.no_push)
    print("[publish] run note: {}".format(run_path))


if __name__ == "__main__":
    main()
