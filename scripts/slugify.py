r"""
Filename generation for archive notes.

Note titles come from external RSS feeds, so the attack surface here is *path
generation*, not text interpretation. Everything that is not [a-z0-9] is
collapsed to a hyphen, which makes `/`, `\`, `..`, NUL and every other path
separator structurally impossible to smuggle through.
"""
import hashlib
import re
import unicodedata

MAX_SLUG_LEN = 60


def make_slug(title):
    """Lowercase ASCII slug. Never contains a path separator."""
    s = unicodedata.normalize("NFKC", title or "").lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    # Japanese-only or symbol-only titles reduce to an empty string; the URL
    # hash appended by make_filename is what keeps those unique.
    return s[:MAX_SLUG_LEN].strip("-") or "post"


def url_hash(url, length=4):
    return hashlib.sha1((url or "").encode("utf-8")).hexdigest()[:length]


def make_filename(date_str, title, url):
    """`<slug>-<hash>.md` — no date in the name.

    Filenames are purely informational (2026-07-23 user decision); the date
    lives in the note frontmatter only. `date_str` is kept in the signature
    for call-site compatibility but no longer participates. The hash
    disambiguates collisions: two outlets titling a story identically would
    otherwise map to one filename and the second would be skipped as
    "already published".
    """
    return "{}-{}.md".format(make_slug(title), url_hash(url))


# Basenames that must never be produced. `claude` is the important one: on a
# case-insensitive filesystem, `_Entities/claude.md` satisfies a lookup for
# CLAUDE.md, so an entity note built from RSS text gets loaded as agent
# instructions. Observed live on 2026-07-21 - the "Claude" entity note was read
# into the session as a directory instruction file. `agents` is the same hazard
# for AGENTS.md; the rest are Windows reserved device names.
RESERVED_BASENAMES = {
    "claude", "agents", "readme", "license",
    "con", "prn", "aux", "nul",
    "com1", "com2", "com3", "com4", "com5", "com6", "com7", "com8", "com9",
    "lpt1", "lpt2", "lpt3", "lpt4", "lpt5", "lpt6", "lpt7", "lpt8", "lpt9",
}


def entity_filename(canonical):
    """Filename for an entity note.

    Entity names are drawn from the alias table, but that table is user-edited
    and the value ends up in a path, so it gets the same treatment. The
    canonical name is preserved inside the note (and in its aliases), so
    `[[Claude]]` still resolves even when the file is claude-entity.md.
    """
    slug = make_slug(canonical)
    if slug in RESERVED_BASENAMES:
        slug += "-entity"
    return slug + ".md"
