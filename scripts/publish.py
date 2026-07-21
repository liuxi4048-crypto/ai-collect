"""
Stage 3: render notes into the Obsidian vault, then commit.

Layout (all under `11_AI Archive/`):

    AI Archive.md               dashboard / landing page
    Maps/Entity Map.canvas      visual, colour-coded entity map
    Runs/<run_id>.md            per-run index with the trend synthesis
    Topics/YYYY/MM/<slug>.md    one note per topic
    Entities/<TypeFolder>/*.md   entity notes, foldered by node type

Design properties:

* **Idempotent.** Re-running after a crash must not duplicate notes or
  double-count co-occurrences. Existing topic files are skipped; entity notes,
  dashboard, and canvas are fully regenerated from the index each run.
* **Scoped.** Only `11_AI Archive/` is ever written or staged. The vault also
  holds a hand-maintained area and another generator's output; neither may be
  touched by an unattended run.
* **Rebuildable.** `--rebuild` reconstructs the index and every derived file
  from the topic notes' own frontmatter, so the folder layout can change
  without losing the graph. Wikilinks are by basename, so moving a note never
  breaks a link.
"""
import argparse
import hashlib
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
from vocab import TAG_GROUPS, filter_tags, primary_group
from collect import load_seen, save_seen, normalize_url

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(REPO_ROOT, "data")
JST = timezone(timedelta(hours=9))

VAULT = os.environ.get("OBSIDIAN_VAULT", r"C:\Users\PC_User\ObsidianVault")
ARCHIVE_DIRNAME = "11_AI Archive"
ARCHIVE_ROOT = os.path.join(VAULT, ARCHIVE_DIRNAME)

# Folder layout inside the archive.
TOPICS_DIR = "Topics"
RUNS_DIR = "Runs"
ENTITIES_DIR = "Entities"
MAPS_DIR = "Maps"
DASHBOARD_NAME = "AI Archive.md"
CANVAS_NAME = "Entity Map.canvas"

ITEMS_PATH = os.path.join(DATA_DIR, "pending_items.json")
NOTES_PATH = os.path.join(DATA_DIR, "pending_notes.json")
INDEX_PATH = os.path.join(DATA_DIR, "entity_index.json")
CANDIDATES_PATH = os.path.join(DATA_DIR, "alias_candidates.json")
LATEST_PATH = os.path.join(DATA_DIR, "latest_run.json")

ENTITY_NOTE_MIN_NOTES = 3     # below this, the node is noise in the graph
TAG_LIST_LIMIT = 30
CANVAS_PER_TYPE = 8

TYPE_LABELS = {"org": "組織", "model": "モデル", "tech": "技術", "benchmark": "ベンチマーク"}
TYPE_ORDER = ("org", "model", "tech", "benchmark")
TYPE_FOLDER = {"org": "Organizations", "model": "Models",
               "tech": "Technologies", "benchmark": "Benchmarks"}
TYPE_EMOJI = {"org": "🏢", "model": "🧠", "tech": "⚙️", "benchmark": "📊"}

# Topic notes are filed by subject, not by date - the date lives in the
# filename (YYYY-MM-DD-...), so a type folder still sorts chronologically. The
# numeric prefix fixes the explorer's order to match the dashboard.
GROUP_FOLDER = {name: "{}_{}".format(i + 1, name)
                for i, (name, _tags) in enumerate(TAG_GROUPS)}
UNCAT_FOLDER = "9_その他"
# Obsidian canvas preset colours: 1 red 2 orange 3 yellow 4 green 5 cyan 6 purple.
TYPE_COLOR = {"org": "5", "model": "4", "tech": "6", "benchmark": "2"}


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


def note_link_target(rel_path):
    """Obsidian resolves links by basename when unique; ours carry a hash."""
    return os.path.splitext(os.path.basename(rel_path))[0]


def topic_group_folder(tags):
    return GROUP_FOLDER.get(primary_group(tags), UNCAT_FOLDER)


def topic_rel_path(tags, filename):
    return "{}/{}/{}".format(TOPICS_DIR, topic_group_folder(tags), filename)


def entity_rel_path(name, etype):
    return "{}/{}/{}".format(ENTITIES_DIR, TYPE_FOLDER[etype], entity_filename(name))


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
        for etype in TYPE_ORDER:
            if etype in grouped:
                links = " · ".join("[[{}]]".format(n) for n in grouped[etype])
                body.append("> **{} {}**: {}".format(TYPE_EMOJI[etype], TYPE_LABELS[etype], links))
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


def entities_by_type(index, threshold=ENTITY_NOTE_MIN_NOTES):
    """{type: [(count, name), ...]} for entities at or above threshold."""
    out = {t: [] for t in TYPE_ORDER}
    for name, rec in index["entities"].items():
        if len(rec["notes"]) >= threshold:
            out.setdefault(rec["type"], []).append((len(rec["notes"]), name))
    for t in out:
        out[t].sort(reverse=True)
    return out


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

    body = ["", "# {} {}".format(TYPE_EMOJI.get(rec["type"], ""), name), ""]
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
        # The one thing Obsidian's backlinks pane cannot show, and the reason
        # these notes are generated at all.
        for other, count in cooccur:
            body.append("- [[{}]] — {}件".format(other, count))
        body.append("")

    if len(notes) > 3:
        body.append("> [!note]- すべての言及（{}件）".format(len(notes)))
        for n in notes:
            body.append("> - `{}` [[{}|{}]]".format(n["date"], note_link_target(n["path"]), n["title_ja"]))
        body.append("")
    return "\n".join(fm + body)


# --- run note ----------------------------------------------------------------
def render_run_note(state, written, skipped_existing, missing, trends, candidates_added):
    run_id = state["run_id"]
    counts = Counter(i["category"] for i in state["selected"])
    tier_a = sum(1 for i in state["selected"] if i["tier"] == "A")
    new_count = len(written) - skipped_existing

    fm = ["---"]
    fm.append("title: {}".format(yaml_str("AI Collect — " + run_id)))
    fm.append("date: {}".format(state["date"]))
    fm.append("run_id: {}".format(run_id))
    fm.append("type: ai-archive-run")
    fm.append("topics: {}".format(len(state["selected"])))
    fm.append("new_notes: {}".format(new_count))
    fm.append("tags:")
    fm.append("  - ai-archive")
    fm.append("  - run-index")
    fm.append("---")

    body = ["", "# 🗂 AI Collect — {}".format(run_id), "",
            "↑ [[AI Archive]]", ""]

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


# --- dashboard ---------------------------------------------------------------
def render_dashboard(index, latest):
    notes = index["notes"]
    ents = index["entities"]
    by_type = entities_by_type(index)
    by_tag_count = Counter()
    by_tag_notes = {}
    for path, m in notes.items():
        for t in m.get("tags", []):
            by_tag_count[t] += 1
            by_tag_notes.setdefault(t, []).append((m["date"], m["title_ja"], path))
    runs = sorted({m["run_id"] for m in notes.values()}, reverse=True)
    updated = max((m["date"] for m in notes.values()), default="—")

    fm = ["---",
          "title: {}".format(yaml_str("AI Archive")),
          "type: ai-archive-home",
          "note_count: {}".format(len(notes)),
          "entity_count: {}".format(len(ents)),
          "run_count: {}".format(len(runs)),
          "updated: {}".format(updated),
          "tags:", "  - ai-archive", "  - home",
          "---", ""]

    b = ["# 🗂 AI Archive", "",
         "> [!abstract] 海外AIニュースの蓄積アーカイブ",
         "> `/ai-collect` で継続収集し、日本語で要約して溜めている。",
         "> **{}** トピック ／ **{}** エンティティ ／ **{}** 回の収集 ・ 最終更新 `{}`".format(
             len(notes), len(ents), len(runs), updated),
         "> ",
         "> `Topics/` は**情報の種類**で分類（モデルと能力／研究と評価／基盤とコスト／"
         "安全性と規制／産業）。ファイル名の日付で各フォルダ内が時系列に並ぶ。",
         ""]

    # Latest trends — the single most valuable thing to surface first.
    if latest and latest.get("trends"):
        b.append("## 📈 最新の潮流 — {}".format(latest.get("run_id", "")))
        b.append("")
        for i, t in enumerate(latest["trends"], 1):
            b.append("**{}. {}**".format(i, t["name"]))
            b.append("")
            b.append("> {}".format(t.get("description", "").strip().replace("\n", " ")))
            b.append("")
        if latest.get("run_id"):
            b.append("→ 全文と関連ノート: [[{}]]".format(latest["run_id"]))
            b.append("")
    elif runs:
        b.append("## 📈 最新の潮流")
        b.append("")
        b.append("→ 最新の収集: [[{}]]".format(runs[0]))
        b.append("")

    # Visual map.
    b.append("## 🗺 ビジュアルマップ")
    b.append("")
    b.append("[[{}|エンティティマップを開く（Canvas）]]".format(os.path.splitext(CANVAS_NAME)[0]))
    b.append("")
    b.append("> [!tip] グラフビューの使い方")
    b.append("> グラフビューで `entity/tech`・`entity/benchmark` のノードを辿ると、")
    b.append("> 別々の組織が同じ技術や評価軸でつながっているところが見える。")
    b.append("> 日付順に読むより発見が多い。")
    b.append("")

    # Top entities by type.
    b.append("## 🕸 主要エンティティ")
    b.append("")
    any_ent = False
    for etype in TYPE_ORDER:
        items = by_type.get(etype, [])[:12]
        if not items:
            continue
        any_ent = True
        line = " · ".join("[[{}]] ({})".format(name, cnt) for cnt, name in items)
        b.append("**{} {}** — {}".format(TYPE_EMOJI[etype], TYPE_LABELS[etype], line))
        b.append("")
    if not any_ent:
        b.append("_まだ閾値（{}件）に達したエンティティがない。_".format(ENTITY_NOTE_MIN_NOTES))
        b.append("")

    # Topics by controlled-vocabulary tag.
    b.append("## 🏷 トピック分類")
    b.append("")
    for group_name, tags in TAG_GROUPS:
        present = [t for t in tags if by_tag_notes.get(t)]
        if not present:
            continue
        b.append("### {}".format(group_name))
        b.append("")
        for t in present:
            rows = sorted(by_tag_notes[t], reverse=True)
            b.append("> [!note]- #{} — {}件".format(t, len(rows)))
            for date, title, path in rows[:TAG_LIST_LIMIT]:
                b.append("> - `{}` [[{}|{}]]".format(date, note_link_target(path), title))
            b.append("")

    # Recent runs.
    b.append("## 🕐 最近の収集")
    b.append("")
    for r in runs[:12]:
        n = sum(1 for m in notes.values() if m["run_id"] == r)
        b.append("- [[{}]] — {}件".format(r, n))
    b.append("")

    return "\n".join(fm + b)


# --- canvas ------------------------------------------------------------------
def _cid(*parts):
    return hashlib.sha1("::".join(parts).encode("utf-8")).hexdigest()[:16]


def render_canvas(index):
    """Obsidian .canvas (JSON): one colour-coded column per node type.

    Deterministic layout so the file only changes when the graph does.
    """
    by_type = entities_by_type(index)
    nodes, edges = [], []
    col_w, card_w, card_h, gap_y = 340, 280, 60, 84

    for ti, etype in enumerate(TYPE_ORDER):
        x = ti * col_w
        header_id = _cid("header", etype)
        nodes.append({
            "id": header_id, "type": "text",
            "text": "## {} {}".format(TYPE_EMOJI[etype], TYPE_LABELS[etype]),
            "x": x, "y": 0, "width": card_w, "height": 80, "color": TYPE_COLOR[etype],
        })
        for j, (cnt, name) in enumerate(by_type.get(etype, [])[:CANVAS_PER_TYPE]):
            nid = _cid("ent", etype, name)
            vault_path = "{}/{}".format(ARCHIVE_DIRNAME, entity_rel_path(name, etype))
            nodes.append({
                "id": nid, "type": "file", "file": vault_path,
                "x": x, "y": 140 + j * gap_y, "width": card_w, "height": card_h,
                "color": TYPE_COLOR[etype],
            })
            edges.append({
                "id": _cid("edge", etype, name),
                "fromNode": header_id, "fromSide": "bottom",
                "toNode": nid, "toSide": "top",
            })

    return json.dumps({"nodes": nodes, "edges": edges}, ensure_ascii=False, indent=2)


# --- frontmatter parser (for --rebuild) --------------------------------------
def parse_frontmatter(path):
    """Minimal reader for the frontmatter shapes this script emits.

    Not a general YAML parser: it understands `key: scalar` and `key:` followed
    by two-space `- item` list entries, which is all these notes contain. Nested
    dict lists (sources) are recognised only enough to be ignored.
    """
    with open(path, encoding="utf-8") as f:
        text = f.read()
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    block = text[3:end].strip("\n").splitlines()

    def unquote(v):
        v = v.strip()
        if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
            return v[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        return v

    out, cur = {}, None
    for line in block:
        if re.match(r"^\S", line) and ":" in line:            # top-level key
            key, _, val = line.partition(":")
            key = key.strip()
            val = val.strip()
            cur = key
            if val == "":
                out[key] = []
            else:
                out[key] = unquote(val)
                cur = None
        elif line.startswith("  - ") and cur in ("tags", "entities", "aliases"):
            out.setdefault(cur, []).append(unquote(line[4:]))
        # other indented lines (sources dict entries) are intentionally skipped
    return out


def canonical_type_map():
    m = {}
    for meta in ent.load_aliases().values():
        m[meta["canonical"]] = meta["type"]
    return m


def rebuild_index_from_vault():
    """Reconstruct the index from the topic notes' own frontmatter."""
    index = empty_index()
    tmap = canonical_type_map()
    topics_root = os.path.join(ARCHIVE_ROOT, TOPICS_DIR)
    unknown = set()
    files = []
    for dirpath, _dirs, names in os.walk(topics_root):
        for n in names:
            if n.endswith(".md"):
                files.append(os.path.join(dirpath, n))
    for abs_path in sorted(files):
        fm = parse_frontmatter(abs_path)
        if fm.get("type") != "ai-archive":
            continue
        rel = os.path.relpath(abs_path, ARCHIVE_ROOT).replace(os.sep, "/")
        ents = []
        for name in fm.get("entities", []):
            etype = tmap.get(name)
            if etype is None:
                unknown.add(name)
                continue
            ents.append({"name": name, "type": etype})
        meta = {
            # Date lives in the filename (YYYY-MM-DD-...) now that folders are
            # by subject, so fall back to the basename, not the path.
            "date": fm.get("date") or os.path.basename(rel)[:10],
            "title_ja": fm.get("title", note_link_target(rel)),
            "tags": filter_tags(fm.get("tags")),
            "tier": fm.get("tier", "B"),
            "run_id": fm.get("run_id", ""),
            "entities": ents,
        }
        index_note(index, rel, meta)
    if unknown:
        print("[publish] rebuild: {} entity names not in the alias table (skipped): {}".format(
            len(unknown), ", ".join(sorted(unknown)[:10])))
    return index


# --- derived-file emission (shared by publish and rebuild) -------------------
def write_entity_notes(index):
    written = 0
    for name, rec in index["entities"].items():
        if len(rec["notes"]) < ENTITY_NOTE_MIN_NOTES:
            continue
        path = safe_join(ARCHIVE_ROOT, ENTITIES_DIR, TYPE_FOLDER[rec["type"]], entity_filename(name))
        write_text(path, render_entity_note(name, rec))
        written += 1
    return written


def write_dashboard_and_map(index):
    latest = load_json(LATEST_PATH, None)
    write_text(safe_join(ARCHIVE_ROOT, DASHBOARD_NAME), render_dashboard(index, latest))
    write_text(safe_join(ARCHIVE_ROOT, MAPS_DIR, CANVAS_NAME), render_canvas(index))


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
        print("[publish] nothing to commit")
        return False
    git("commit", "-q", "-m", message)
    if push:
        git("push", "-q")
        print("[publish] pushed")
    else:
        print("[publish] committed (push skipped)")
    return True


# --- main --------------------------------------------------------------------
def do_rebuild(args):
    index = rebuild_index_from_vault()
    ecount = write_entity_notes(index)
    write_dashboard_and_map(index)
    write_json(INDEX_PATH, index)
    print("[publish] rebuild: {} topics, {} entities, {} entity notes".format(
        len(index["notes"]), len(index["entities"]), ecount))
    if not args.no_commit:
        commit_and_push("ai-collect: rebuild archive index & layout", push=not args.no_push)


def do_publish(args):
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
    date = state["date"]
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

        tags = filter_tags(note.get("tags"))
        filename = make_filename(date, item["representative_title"], item["representative_url"])
        rel_path = topic_rel_path(tags, filename)
        abs_path = safe_join(ARCHIVE_ROOT, TOPICS_DIR, topic_group_folder(tags), filename)

        if os.path.exists(abs_path):
            skipped_existing += 1
        else:
            write_text(abs_path, render_topic_note(dict(item, date=date), note, run_id))

        # Recorded only after the file exists, so a crash mid-run leaves the
        # remaining items collectable rather than silently marked done.
        for a in item["articles"]:
            seen[normalize_url(a["link"])] = date
        save_seen(seen)

        meta = {
            "date": date, "title_ja": note["title_ja"],
            "tags": tags, "tier": item["tier"],
            "run_id": run_id, "entities": item.get("entities", []),
        }
        index_note(index, rel_path, meta)
        written[idx] = {"path": rel_path, "title_ja": note["title_ja"], "tier": item["tier"]}

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
                "type": t.get("type", "unknown"), "count": 0, "first_seen": date,
                "example": t.get("context", "")[:200],
            })
            rec["count"] += 1
            rec["last_seen"] = date
            candidates_added += 1
        write_json(CANDIDATES_PATH, cands)

    # Persist this run's trends so the dashboard can surface them (and so a
    # later --rebuild still shows the most recent synthesis).
    if trends:
        write_json(LATEST_PATH, {"run_id": run_id, "date": date, "trends": trends})

    run_path = safe_join(ARCHIVE_ROOT, RUNS_DIR, run_id + ".md")
    write_text(run_path, render_run_note(state, written, skipped_existing, missing,
                                         trends, candidates_added))
    ecount = write_entity_notes(index)
    write_dashboard_and_map(index)
    write_json(INDEX_PATH, index)

    print("[publish] {} new notes, {} skipped (existing), {} unsummarized".format(
        len(written) - skipped_existing, skipped_existing, len(missing)))
    print("[publish] {} entity notes, {} total archive notes".format(ecount, len(index["notes"])))
    if missing:
        print("[publish] unsummarized indexes: {}".format(missing))
    if not args.no_commit:
        commit_and_push("ai-collect: {} ({}件)".format(run_id, len(written)), push=not args.no_push)
    print("[publish] run note: {}".format(run_path))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--items", default=ITEMS_PATH)
    ap.add_argument("--notes", default=NOTES_PATH)
    ap.add_argument("--rebuild", action="store_true",
                    help="Rebuild index and all derived files from topic-note frontmatter.")
    ap.add_argument("--no-push", action="store_true")
    ap.add_argument("--no-commit", action="store_true")
    args = ap.parse_args()

    if not os.path.isdir(VAULT):
        raise SystemExit("Vault not found: {}".format(VAULT))
    if args.rebuild:
        do_rebuild(args)
    else:
        do_publish(args)


if __name__ == "__main__":
    main()
