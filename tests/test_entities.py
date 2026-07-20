import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import entities as ent


def names(text):
    return [e["name"] for e in ent.extract(text)]


def types(text):
    return {e["name"]: e["type"] for e in ent.extract(text)}


def test_spelling_variants_collapse_to_one_node():
    # The whole point: one real entity must never split into several nodes.
    for spelling in ["Anthropic", "anthropic", "ANTHROPIC", "Ａｎｔｈｒｏｐｉｃ"]:
        assert "Anthropic" in names("A story about " + spelling)


def test_all_four_node_types_are_reachable():
    got = types("OpenAI released GPT-5 using mixture-of-experts, topping SWE-bench")
    assert got.get("OpenAI") == "org"
    assert got.get("GPT-5") == "model"
    assert got.get("Mixture of Experts") == "tech"
    assert got.get("SWE-bench") == "benchmark"


def test_model_implies_its_org():
    # An article about Llama is an article about Meta even if unnamed.
    assert "Meta" in names("Llama 4 weights are out")
    assert "Anthropic" in names("Claude Opus 4.8 scores well")


def test_longest_alias_wins():
    got = names("Claude Opus 4.8 is here")
    assert "Claude Opus 4.8" in got
    assert "Claude" not in got


def test_short_aliases_do_not_match_inside_words():
    assert "RAG" not in names("cloud storage is ragged and fragmented")
    assert "RAG" in names("a RAG pipeline")
    assert "RAG" in names("retrieval-augmented generation works")


def test_no_entities_in_unrelated_text():
    assert names("the weather is nice today and nothing else happened") == []


def test_alias_table_is_wellformed():
    table = ent.load_aliases()
    assert len(table) > 100
    for alias, meta in table.items():
        assert alias == alias.lower(), alias
        assert meta["type"] in ent.VALID_TYPES
        # An implied org must itself be a known canonical, or the graph gets a
        # dangling node nothing ever links back to.
        if meta.get("org"):
            assert meta["org"] in set(ent.known_canonicals()), meta["org"]


def test_extract_from_articles_merges_a_cluster():
    arts = [{"title": "OpenAI ships", "summary": "about GPT-5"},
            {"title": "Rivals respond", "summary": "Anthropic and Google react"}]
    got = [e["name"] for e in ent.extract_from_articles(arts)]
    assert {"OpenAI", "GPT-5", "Anthropic", "Google"} <= set(got)
