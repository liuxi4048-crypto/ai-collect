"""
Controlled vocabulary for note tags.

Feed-derived categories are too coarse to navigate (every arXiv item lands in
"research"), so tags are restricted to this fixed list. Anything outside it is
dropped at publish time rather than silently creating a new MOC bucket.
"""

TAGS = [
    "llm",              # models and their releases
    "agent",            # autonomous / tool-using systems
    "inference-cost",   # serving cost, quantization, efficiency
    "training",         # pretraining, fine-tuning, RL
    "multimodal",       # vision, audio, video
    "open-weights",     # openly released weights
    "benchmark",        # evaluation results and eval design
    "safety",           # alignment, misuse, red-teaming
    "security",         # vulnerabilities, attacks on systems
    "regulation",       # law, policy, government
    "chips",            # silicon, datacenter hardware
    "infrastructure",   # cloud, serving stacks, energy
    "robotics",
    "coding",           # developer tooling, code models
    "enterprise",       # business adoption, SaaS products
    "funding",          # raises, valuations, M&A
    "research",         # papers without a clearer bucket
    "product",          # consumer-facing launches
]

TAG_SET = set(TAGS)

# Bucketed for the MOC so the index reads in a sensible order rather than
# alphabetically.
TAG_GROUPS = [
    ("モデルと能力", ["llm", "multimodal", "open-weights", "agent", "coding"]),
    ("研究と評価", ["research", "benchmark", "training"]),
    ("基盤とコスト", ["inference-cost", "chips", "infrastructure"]),
    ("安全性と規制", ["safety", "security", "regulation"]),
    ("産業", ["product", "enterprise", "funding", "robotics"]),
]


def filter_tags(tags):
    """Keep only known tags, deduped, in TAGS order."""
    if not tags:
        return []
    got = {t.strip().lower() for t in tags if isinstance(t, str)}
    return [t for t in TAGS if t in got]


# Every controlled tag belongs to exactly one topical group. This is what lets
# each topic note have a single physical home folder chosen by its subject
# rather than its date.
TAG_TO_GROUP = {t: name for name, tags in TAG_GROUPS for t in tags}


def primary_group(tags):
    """The group a note is filed under, from its highest-priority tag.

    `tags` arrives in TAGS order (filter_tags), which is roughly centrality
    order, so the first tag is a reasonable primary. A note still appears under
    every one of its tags in the dashboard; the folder is only its home.
    Returns None for an untagged note (caller files it as uncategorized).
    """
    for t in tags:
        if t in TAG_TO_GROUP:
            return TAG_TO_GROUP[t]
    return None
