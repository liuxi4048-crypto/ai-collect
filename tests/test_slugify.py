import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from slugify import make_slug, make_filename, entity_filename, MAX_SLUG_LEN


def test_no_path_separators_survive():
    for hostile in ["../../etc/passwd", r"..\..\windows\system32", "a/b/c", "x\\y",
                    "foo\x00bar", "....//....//x"]:
        slug = make_slug(hostile)
        assert "/" not in slug and "\\" not in slug
        assert ".." not in slug
        assert "\x00" not in slug


def test_empty_and_non_ascii_titles_get_a_fallback():
    assert make_slug("") == "post"
    assert make_slug("   ") == "post"
    assert make_slug("日本語だけの見出し") == "post"
    assert make_slug("!!!???") == "post"


def test_length_is_capped():
    assert len(make_slug("word " * 200)) <= MAX_SLUG_LEN


def test_filename_disambiguates_identical_titles():
    a = make_filename("2026-07-21", "OpenAI ships a thing", "https://a.example/1")
    b = make_filename("2026-07-21", "OpenAI ships a thing", "https://b.example/2")
    assert a != b
    assert a.startswith("2026-07-21-openai-ships-a-thing-")
    assert a.endswith(".md")


def test_filename_is_stable_for_the_same_url():
    args = ("2026-07-21", "Title", "https://example.com/x")
    assert make_filename(*args) == make_filename(*args)


def test_japanese_title_still_yields_a_unique_filename():
    a = make_filename("2026-07-21", "日本語", "https://a.example/1")
    b = make_filename("2026-07-21", "日本語", "https://a.example/2")
    assert a != b
    assert a.startswith("2026-07-21-post-")


def test_entity_filename_is_sanitized():
    assert entity_filename("Google DeepMind") == "google-deepmind.md"
    assert "/" not in entity_filename("a/b")


def test_entity_filename_never_shadows_an_instruction_file():
    """A note named claude.md is read as CLAUDE.md on Windows.

    That turns an entity note built from untrusted RSS text into agent
    instructions. Regression guard for a live incident on 2026-07-21.
    """
    for name in ["Claude", "claude", "CLAUDE", "Agents", "readme"]:
        base = entity_filename(name).lower()
        assert base not in ("claude.md", "agents.md", "readme.md"), name


def test_reserved_escape_keeps_the_name_recoverable():
    assert entity_filename("Claude") == "claude-entity.md"
    assert entity_filename("Claude Code") == "claude-code.md"   # not reserved
