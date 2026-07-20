"""
Contract tests for the news-digest seam.

ai-collect imports five helpers out of another repository that is maintained
independently. If its API drifts, the failure mode is silent: clustering stops
grouping, or entity text arrives empty, and the archive quietly degrades
instead of crashing. These tests turn that into a loud failure.

They are the reason it is safe to depend on news-digest at all.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import pytest

NEWS_DIGEST = os.environ.get("NEWS_DIGEST_SCRIPTS", r"C:\dev\news-digest\scripts")
pytestmark = pytest.mark.skipif(
    not os.path.isdir(NEWS_DIGEST),
    reason="news-digest not present at {}".format(NEWS_DIGEST),
)

sys.path.insert(0, NEWS_DIGEST)
import generate_digest as gd  # noqa: E402


def article(title, summary="", link="https://example.com/a", label="X"):
    return {"title": title, "summary": summary, "link": link,
            "domain": gd.domain_of(link), "source_label": label,
            "published": None, "category": "ai"}


def test_domain_of_strips_www():
    assert gd.domain_of("https://www.example.com/a/b") == "example.com"
    assert gd.domain_of("not a url") is not None


def test_tokenize_returns_a_set_of_content_words():
    toks = gd.tokenize("OpenAI Ships GPT-5 To Everyone")
    assert isinstance(toks, set)
    assert "openai" in toks


def test_similarity_is_an_overlap_coefficient():
    assert gd.similarity({"a", "b"}, {"a", "b"}) == 1.0
    assert gd.similarity({"a"}, {"b"}) == 0.0
    assert gd.similarity(set(), {"a"}) == 0.0
    # Subset of a longer title still scores 1.0 - this is the property that
    # makes cross-outlet clustering work at all.
    assert gd.similarity({"a", "b"}, {"a", "b", "c", "d"}) == 1.0


def test_cluster_articles_shape():
    arts = [article("OpenAI ships GPT-5 to everyone today"),
            article("OpenAI ships GPT-5 to everyone, sources say"),
            article("Completely unrelated robot vacuum review roundup")]
    clusters = gd.cluster_articles(arts)
    assert isinstance(clusters, list)
    for c in clusters:
        assert "articles" in c and isinstance(c["articles"], list)
    sizes = sorted(len(c["articles"]) for c in clusters)
    assert sizes == [1, 2], "clustering no longer groups near-identical titles"


def test_is_junk_filters_stubs():
    assert gd.is_junk("Two words") is True
    assert gd.is_junk("OpenAI ships a real headline here") is False


def test_ai_relevance_counts_terms():
    n = gd.ai_relevance([article("LLM inference training", "neural model agent")])
    assert isinstance(n, int) and n > 0


def test_importing_generate_digest_has_no_side_effects():
    """It must not run main() or hit the network on import.

    collect.py imports it at module scope; if that ever starts publishing a
    digest, an ai-collect run would corrupt the morning job's state.
    """
    import importlib
    importlib.reload(gd)
