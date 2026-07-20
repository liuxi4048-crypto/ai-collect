"""
Deterministic, multi-type entity extraction.

Two rules drive this module:

1. **Extract from the original English.** Running a matcher over the Japanese
   summary would split one real entity across every translation the model
   happened to pick ("Anthropic" / "アンソロピック" / "Anthropic社"), which
   fragments the graph permanently. Extraction happens on title+summary of the
   source articles, before any translation.

2. **No LLM in the loop.** The same article must always yield the same nodes,
   otherwise notes written months apart disagree about what they link to.

Node types are org / model / tech / benchmark. Techs and benchmarks are what
make the graph more than a star around a handful of company names: they are
the nodes that different orgs actually share.
"""
import json
import os
import re
import unicodedata

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
ALIASES_PATH = os.path.join(DATA_DIR, "canonical_aliases.json")

VALID_TYPES = ("org", "model", "tech", "benchmark")


def load_aliases(path=ALIASES_PATH):
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    table = {}
    for alias, meta in raw.items():
        if alias.startswith("_"):
            continue
        if not isinstance(meta, dict) or "canonical" not in meta:
            raise ValueError("Bad alias entry: {!r}".format(alias))
        if meta.get("type") not in VALID_TYPES:
            raise ValueError("Bad type for alias {!r}: {!r}".format(alias, meta.get("type")))
        table[alias.lower()] = meta
    return table


def _build_pattern(aliases):
    """One alternation, longest alias first so 'claude opus 4.8' beats 'claude'.

    The boundaries are explicit lookarounds rather than \\b because many aliases
    end in punctuation ('x.ai', 'swe-bench') where \\b behaves unintuitively.
    Blocking only [a-z0-9] on either side is what keeps 'rag' out of 'storage'
    and 'ragged' while still matching 'RAG,' or '(RAG)'.
    """
    ordered = sorted(aliases, key=len, reverse=True)
    body = "|".join(re.escape(a) for a in ordered)
    return re.compile(r"(?<![a-z0-9])(" + body + r")(?![a-z0-9])")


_CACHE = {}


def get_matcher(path=ALIASES_PATH):
    if path not in _CACHE:
        aliases = load_aliases(path)
        _CACHE[path] = (aliases, _build_pattern(aliases))
    return _CACHE[path]


def normalize(text):
    return unicodedata.normalize("NFKC", text or "").lower()


def extract(text, path=ALIASES_PATH):
    """Return entities as a list of {name, type} dicts, deduped, stable order.

    A matched model also pulls in its org: an article about "Llama 4" is an
    article about Meta whether or not it spells the company out. This is where
    a lot of the cross-company structure in the graph comes from.
    """
    aliases, pattern = get_matcher(path)
    haystack = normalize(text)

    found = {}   # canonical -> type
    order = []

    def add(name, etype):
        if name not in found:
            found[name] = etype
            order.append(name)

    for match in pattern.finditer(haystack):
        meta = aliases[match.group(1)]
        add(meta["canonical"], meta["type"])
        implied_org = meta.get("org")
        if implied_org:
            add(implied_org, "org")

    return [{"name": n, "type": found[n]} for n in order]


def extract_from_articles(articles, path=ALIASES_PATH):
    """Entities across every article in a cluster (title + summary)."""
    blob = "\n".join(
        "{} {}".format(a.get("title", ""), a.get("summary", ""))
        for a in articles
    )
    return extract(blob, path=path)


def known_canonicals(path=ALIASES_PATH):
    aliases, _ = get_matcher(path)
    return sorted({m["canonical"] for m in aliases.values()})


def aliases_for(canonical, path=ALIASES_PATH):
    """Every spelling that maps to a canonical name (for note frontmatter)."""
    aliases, _ = get_matcher(path)
    return sorted(a for a, m in aliases.items() if m["canonical"] == canonical)
