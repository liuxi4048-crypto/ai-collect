"""
Feed definitions for the *dev-collect* routine.

Sibling of feeds.py, but a different mission. feeds.py archives AI news through
a strict relevance gate; this list gathers the material a builder wants while
actually building systems and apps - framework releases, changelogs, cloud and
infra posts, database notes, security advisories, and practitioner writing -
and dev_collect.py runs it WITHOUT that gate. "Indiscriminate" is the point:
the archive should catch a Postgres point-release or a Vite RC even though
neither mentions a single AI keyword.

`category` here is a build-domain bucket (web / backend / language / cloud /
devops / data / security / mobile / ai-dev / general), used only for grouping
and dashboard facets - there is no research cap and no arXiv flood to balance.

`primary` marks first-party sources (the project's own blog / release feed).
They get a scoring bonus for the same reason as in feeds.py: a release is
announced once by its own project and never "confirmed by three outlets".

Feeds that 404 do not break a run - dev_collect.fetch reports them as FAILED
and the cycle proceeds. Prune dead entries here rather than leaving them to
silently shrink coverage.
"""

DEFAULT_WINDOW_HOURS = 96
SLOW_WINDOW_HOURS = 336      # project blogs and personal sites post rarely
FAST_WINDOW_HOURS = 48       # high-volume aggregators


def feed(url, label, category, window=DEFAULT_WINDOW_HOURS, primary=False, weight=1.0):
    return {
        "url": url,
        "label": label,
        "category": category,
        "window_hours": window,
        "primary": primary,
        "weight": weight,
    }


# category: web / backend / language / cloud / devops / data / security / mobile / ai-dev / general
DEV_FEEDS = [
    # --- aggregators / practitioners -----------------------------------------
    feed("https://hnrss.org/frontpage", "Hacker News", "general", FAST_WINDOW_HOURS, weight=1.0),
    feed("https://dev.to/feed", "DEV Community", "general", FAST_WINDOW_HOURS, weight=0.8),
    feed("https://github.blog/feed/", "GitHub Blog", "general", SLOW_WINDOW_HOURS, primary=True, weight=1.2),
    feed("https://stackoverflow.blog/feed/", "Stack Overflow Blog", "general", SLOW_WINDOW_HOURS, weight=1.0),
    feed("https://changelog.com/feed", "The Changelog", "general", SLOW_WINDOW_HOURS, weight=1.0),
    feed("https://martinfowler.com/feed.atom", "Martin Fowler", "backend", SLOW_WINDOW_HOURS, weight=1.2),
    feed("https://www.theregister.com/software/headlines.atom", "The Register SW", "general", FAST_WINDOW_HOURS, weight=0.8),

    # --- web / frontend ------------------------------------------------------
    feed("https://web.dev/feed.xml", "web.dev", "web", SLOW_WINDOW_HOURS, primary=True, weight=1.2),
    feed("https://css-tricks.com/feed/", "CSS-Tricks", "web", SLOW_WINDOW_HOURS, weight=1.0),
    feed("https://www.smashingmagazine.com/feed/", "Smashing Magazine", "web", SLOW_WINDOW_HOURS, weight=1.0),
    feed("https://vercel.com/atom", "Vercel", "web", SLOW_WINDOW_HOURS, primary=True, weight=1.1),
    feed("https://react.dev/rss.xml", "React", "web", SLOW_WINDOW_HOURS, primary=True, weight=1.2),

    # --- languages / runtimes ------------------------------------------------
    feed("https://blog.rust-lang.org/feed.xml", "Rust Blog", "language", SLOW_WINDOW_HOURS, primary=True, weight=1.3),
    feed("https://go.dev/blog/feed.atom", "Go Blog", "language", SLOW_WINDOW_HOURS, primary=True, weight=1.3),
    feed("https://blog.python.org/feeds/posts/default", "Python Insider", "language", SLOW_WINDOW_HOURS, primary=True, weight=1.3),
    feed("https://nodejs.org/en/feed/blog.xml", "Node.js", "language", SLOW_WINDOW_HOURS, primary=True, weight=1.2),
    feed("https://devblogs.microsoft.com/typescript/feed/", "TypeScript", "language", SLOW_WINDOW_HOURS, primary=True, weight=1.2),

    # --- cloud / infra -------------------------------------------------------
    feed("https://aws.amazon.com/blogs/aws/feed/", "AWS News", "cloud", DEFAULT_WINDOW_HOURS, primary=True, weight=1.2),
    feed("https://cloudblog.withgoogle.com/rss/", "Google Cloud", "cloud", DEFAULT_WINDOW_HOURS, primary=True, weight=1.1),
    feed("https://blog.cloudflare.com/rss/", "Cloudflare", "cloud", SLOW_WINDOW_HOURS, primary=True, weight=1.2),

    # --- devops / platform ---------------------------------------------------
    feed("https://kubernetes.io/feed.xml", "Kubernetes", "devops", SLOW_WINDOW_HOURS, primary=True, weight=1.2),
    feed("https://www.docker.com/blog/feed/", "Docker", "devops", SLOW_WINDOW_HOURS, primary=True, weight=1.1),
    feed("https://github.blog/changelog/feed/", "GitHub Changelog", "devops", FAST_WINDOW_HOURS, primary=True, weight=1.0),

    # --- data / databases ----------------------------------------------------
    feed("https://www.postgresql.org/news.rss", "PostgreSQL", "data", SLOW_WINDOW_HOURS, primary=True, weight=1.2),
    feed("https://planetscale.com/blog/rss.xml", "PlanetScale", "data", SLOW_WINDOW_HOURS, weight=1.0),
    feed("https://www.mongodb.com/blog/rss", "MongoDB", "data", SLOW_WINDOW_HOURS, primary=True, weight=1.0),

    # --- security ------------------------------------------------------------
    feed("https://www.bleepingcomputer.com/feed/", "BleepingComputer", "security", FAST_WINDOW_HOURS, weight=1.0),
    feed("https://github.blog/security/feed/", "GitHub Security", "security", SLOW_WINDOW_HOURS, primary=True, weight=1.1),

    # --- mobile --------------------------------------------------------------
    feed("https://android-developers.googleblog.com/feeds/posts/default", "Android Developers", "mobile", SLOW_WINDOW_HOURS, primary=True, weight=1.1),

    # --- ai for builders -----------------------------------------------------
    # Not an AI-news mirror (that is what ai-collect is for) - only the
    # developer-tooling angle: SDKs, coding agents, local inference for apps.
    feed("https://github.blog/ai-and-ml/feed/", "GitHub AI/ML", "ai-dev", SLOW_WINDOW_HOURS, primary=True, weight=1.0),
    feed("https://huggingface.co/blog/feed.xml", "Hugging Face", "ai-dev", SLOW_WINDOW_HOURS, primary=True, weight=1.0),
]


def by_label(label):
    for f in DEV_FEEDS:
        if f["label"] == label:
            return f
    return None
