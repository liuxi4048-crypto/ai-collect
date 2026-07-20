"""
Feed definitions for ai-collect.

Wider than news-digest's list on purpose: this system archives rather than
curates, so breadth matters more than signal-to-noise per feed.

`primary` marks first-party sources (labs, arXiv). Those get a scoring bonus
because corroboration-based ranking systematically under-rates them: a single
important paper is published once and never "confirmed by 3 outlets", while a
routine press release gets syndicated everywhere.

Every URL here was checked live before being committed. Feeds that 404 are
removed rather than left in - news-digest carried dead entries for a while and
they silently shrank coverage.
"""

DEFAULT_WINDOW_HOURS = 72
SLOW_WINDOW_HOURS = 240      # labs and personal blogs post weekly at best
FAST_WINDOW_HOURS = 36       # high-volume aggregators


def feed(url, label, category, window=DEFAULT_WINDOW_HOURS, primary=False, weight=1.0):
    return {
        "url": url,
        "label": label,
        "category": category,
        "window_hours": window,
        "primary": primary,
        "weight": weight,
    }


# category: ai / research / industry / it
FEEDS = [
    # --- first-party labs -----------------------------------------------------
    feed("https://openai.com/news/rss.xml", "OpenAI", "ai", SLOW_WINDOW_HOURS, primary=True, weight=1.6),
    feed("https://blog.google/technology/ai/rss/", "Google AI Blog", "ai", SLOW_WINDOW_HOURS, primary=True, weight=1.5),
    feed("https://deepmind.google/blog/rss.xml", "Google DeepMind", "ai", SLOW_WINDOW_HOURS, primary=True, weight=1.6),
    feed("https://research.google/blog/rss/", "Google Research", "research", SLOW_WINDOW_HOURS, primary=True, weight=1.4),
    feed("https://huggingface.co/blog/feed.xml", "Hugging Face", "ai", SLOW_WINDOW_HOURS, primary=True, weight=1.4),
    feed("https://blogs.nvidia.com/feed/", "NVIDIA Blog", "industry", SLOW_WINDOW_HOURS, primary=True, weight=1.2),
    feed("https://www.microsoft.com/en-us/research/feed/", "Microsoft Research", "research", SLOW_WINDOW_HOURS, primary=True, weight=1.3),
    feed("https://bair.berkeley.edu/blog/feed.xml", "BAIR Blog", "research", SLOW_WINDOW_HOURS, primary=True, weight=1.2),

    # --- research -------------------------------------------------------------
    # arxiv.org serves a chain the Windows store does not complete, so these
    # only work because fetch() below verifies against the certifi bundle.
    # news-digest recorded "arXiv RSS (empty)" and dropped it; the feed was
    # never empty, the TLS handshake was failing and the error was swallowed.
    feed("https://export.arxiv.org/api/query?search_query=cat:cs.AI&sortBy=submittedDate&sortOrder=descending&max_results=60",
         "arXiv cs.AI", "research", DEFAULT_WINDOW_HOURS, primary=True, weight=1.3),
    feed("https://export.arxiv.org/api/query?search_query=cat:cs.CL&sortBy=submittedDate&sortOrder=descending&max_results=60",
         "arXiv cs.CL", "research", DEFAULT_WINDOW_HOURS, primary=True, weight=1.3),
    feed("https://export.arxiv.org/api/query?search_query=cat:cs.LG&sortBy=submittedDate&sortOrder=descending&max_results=60",
         "arXiv cs.LG", "research", DEFAULT_WINDOW_HOURS, primary=True, weight=1.2),

    # --- AI trade press -------------------------------------------------------
    feed("https://techcrunch.com/category/artificial-intelligence/feed/", "TechCrunch AI", "ai", weight=1.2),
    feed("https://venturebeat.com/category/ai/feed/", "VentureBeat AI", "ai", weight=1.1),
    feed("https://arstechnica.com/ai/feed/", "Ars Technica AI", "ai", weight=1.2),
    feed("https://the-decoder.com/feed/", "The Decoder", "ai", weight=1.1),
    feed("https://www.technologyreview.com/topic/artificial-intelligence/feed", "MIT Tech Review AI", "ai", weight=1.3),
    feed("https://aibusiness.com/rss.xml", "AI Business", "industry"),
    feed("https://www.marktechpost.com/feed/", "MarkTechPost", "ai", weight=0.9),
    feed("https://syncedreview.com/feed/", "Synced", "ai", weight=0.9),

    # --- practitioners --------------------------------------------------------
    feed("https://simonwillison.net/atom/everything/", "Simon Willison", "ai", SLOW_WINDOW_HOURS, weight=1.2),
    # hnrss drops the connection on filtered queries (?points=150); the plain
    # front page is the only endpoint that answers reliably.
    feed("https://hnrss.org/frontpage", "Hacker News", "it", FAST_WINDOW_HOURS, weight=1.0),

    # --- general tech ---------------------------------------------------------
    feed("https://techcrunch.com/feed/", "TechCrunch", "industry", FAST_WINDOW_HOURS, weight=0.9),
    feed("https://www.theverge.com/rss/index.xml", "The Verge", "industry", FAST_WINDOW_HOURS, weight=0.9),
    feed("https://feeds.arstechnica.com/arstechnica/technology-lab", "Ars Technica Tech", "it", weight=0.9),
    feed("https://www.wired.com/feed/tag/ai/latest/rss", "WIRED AI", "ai", weight=1.0),
    feed("https://spectrum.ieee.org/feeds/topic/artificial-intelligence.rss", "IEEE Spectrum AI", "research", weight=1.1),
]
# Removed after live checks on 2026-07-21: InfoQ AI (404).


def by_label(label):
    for f in FEEDS:
        if f["label"] == label:
            return f
    return None
