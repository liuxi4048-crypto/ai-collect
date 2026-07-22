"""
Controlled vocabulary for dev-collect note tags.

Separate from vocab.py: that list is about AI capabilities (llm, training,
open-weights...); this one is about the concerns of someone building a system
or an app. Tags outside this list are dropped at publish time rather than
silently creating a new facet.
"""

DEV_TAGS = [
    "release",        # new version, GA, RC, changelog
    "framework",      # web/app frameworks and their ecosystems
    "language",       # language & runtime news
    "frontend",       # UI, browser, CSS, rendering
    "backend",        # servers, APIs, services
    "api",            # API design, SDKs, protocols
    "database",       # SQL/NoSQL, storage engines
    "cloud",          # cloud platforms & managed services
    "devops",         # CI/CD, containers, orchestration, platform
    "infra",          # networking, edge, performance infrastructure
    "security",       # vulnerabilities, advisories, hardening
    "testing",        # testing, QA, reliability
    "tooling",        # build tools, editors, dev utilities
    "performance",    # speed, memory, optimization
    "architecture",   # patterns, system design
    "mobile",         # iOS/Android/cross-platform
    "data",           # data engineering, analytics, pipelines
    "ai-dev",         # AI as a building block: SDKs, agents, local inference
    "tutorial",       # how-to, guides, deep dives
]

DEV_TAG_SET = set(DEV_TAGS)


def filter_tags(tags):
    """Keep only known tags, deduped, in DEV_TAGS order."""
    if not tags:
        return []
    got = {t.strip().lower() for t in tags if isinstance(t, str)}
    return [t for t in DEV_TAGS if t in got]
