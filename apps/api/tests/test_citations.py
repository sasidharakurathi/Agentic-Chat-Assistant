"""Citation registry + marker resolution (task 2.9).

Pure unit tests — no DB, no embedder. Everything here is about the two things
that can go silently wrong: handing out a marker that means something
different from what the model thinks it means, and reading a ``[n]`` out of
text that was never a citation at all. Both produce a confident, wrong
attribution with no error anywhere, which is why they get this much coverage.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict

from app.agent.citations import Citation, CitationRegistry, blocks_from
from app.rag.retrieve import RetrievedChunk


def chunk(title: str = "Handbook", **kw: object) -> RetrievedChunk:
    defaults: dict = {
        "chunk_id": uuid.uuid4(),
        "document_id": uuid.uuid4(),
        "data_source_id": uuid.uuid4(),
        "title": title,
        "uri": None,
        "content": f"{title} says refunds take five business days.",
        "score": 0.8,
        "source_type": "text",
    }
    defaults.update(kw)
    return RetrievedChunk(**defaults)  # type: ignore[arg-type]


# ── numbering ────────────────────────────────────────────────


def test_markers_continue_across_calls_instead_of_restarting() -> None:
    """The reason this class exists. kb_search numbers its own results from 1
    every call, so two searches in one answer would both produce a [1]."""
    reg = CitationRegistry()
    first = reg.register([chunk("A"), chunk("B")])
    second = reg.register([chunk("C"), chunk("D")])
    assert first == [1, 2]
    assert second == [3, 4]


def test_the_same_chunk_keeps_its_marker_when_retrieved_again() -> None:
    reg = CitationRegistry()
    shared = chunk("Shared")
    assert reg.register([shared, chunk("Other")]) == [1, 2]
    # A later search surfacing the same chunk must not mint a second number
    # for it — the model has already been shown it as [1].
    assert reg.register([chunk("New"), shared]) == [3, 1]


def test_a_resumed_conversation_never_reuses_a_live_marker() -> None:
    """The SDK session is resumed across turns, so turn 1's numbered results
    are still in the model's context. Turn 2 must continue, not restart."""
    turn1 = CitationRegistry()
    turn1.register([chunk("A"), chunk("B"), chunk("C")])

    turn2 = CitationRegistry(turn1.dump())
    assert turn2.register([chunk("D")]) == [4]


def test_a_chunk_cited_in_an_earlier_turn_still_resolves_in_a_later_one() -> None:
    """The failure this guards against: the model answers turn 2 partly from
    context, reusing [1] from turn 1. It must still mean turn 1's chunk."""
    first = chunk("Refund Policy")
    turn1 = CitationRegistry()
    turn1.register([first])

    turn2 = CitationRegistry(turn1.dump())
    turn2.register([first, chunk("Exchange Policy")])
    resolved = turn2.resolve("Refunds take five days [1], exchanges ten [2].")
    assert [c.marker for c in resolved] == [1, 2]
    assert resolved[0].title == "Refund Policy"
    assert resolved[0].chunk_id == str(first.chunk_id)


def test_dump_is_bounded_but_keeps_the_newest_markers() -> None:
    from app.agent.citations import MAX_TRACKED

    reg = CitationRegistry()
    reg.register([chunk(f"c{i}") for i in range(MAX_TRACKED + 25)])
    state = reg.dump()
    assert len(state["markers"]) == MAX_TRACKED
    assert state["next"] == MAX_TRACKED + 26
    # The entries kept are the most recent ones, and `next` still clears them.
    assert max(state["markers"].values()) == MAX_TRACKED + 25


def test_a_malformed_seed_cannot_hand_out_a_live_marker() -> None:
    reg = CitationRegistry({"markers": {"abc": 9}, "next": 2})
    assert reg.register([chunk("X")]) == [10]


# ── marker parsing ───────────────────────────────────────────


def _resolve(text: str, n: int = 3) -> list[Citation]:
    reg = CitationRegistry()
    reg.register([chunk(f"src{i}") for i in range(1, n + 1)])
    return reg.resolve(text)


def test_plain_and_grouped_markers_resolve() -> None:
    assert [c.marker for c in _resolve("Refunds take five days [1].")] == [1]
    assert [c.marker for c in _resolve("Both agree [2, 3].")] == [2, 3]
    assert [c.marker for c in _resolve("Sequential [1][2].")] == [1, 2]


def test_markers_are_returned_in_first_use_order_not_numeric_order() -> None:
    assert [c.marker for c in _resolve("First [3], then [1].")] == [3, 1]


def test_a_repeated_marker_appears_once_but_records_every_span() -> None:
    resolved = _resolve("Refunds [1] and exchanges [1] both apply.")
    assert len(resolved) == 1
    assert len(resolved[0].spans) == 2


def test_array_indices_in_code_are_not_citations() -> None:
    """The false positive that would fabricate attribution: this assistant can
    answer coding questions, and `data[1]` is not a citation."""
    fenced = "Here you go:\n```python\nrows = data[1]\nprint(cols[2])\n```\nThat's it."
    assert _resolve(fenced) == []
    assert _resolve("Use `arr[1]` for that.") == []
    assert _resolve("Read items[1] and then a[0][2].") == []


def test_a_hallucinated_marker_is_dropped_not_guessed() -> None:
    assert [c.marker for c in _resolve("Per policy [7].", n=3)] == []


def test_markdown_links_and_reference_definitions_still_count() -> None:
    # These are genuinely ambiguous, but a model writing "[1](url)" after a
    # kb_search is far more likely citing than authoring a link named "1".
    assert [c.marker for c in _resolve("See [1](https://example.com).")] == [1]


def test_spans_are_offsets_into_the_text_that_was_resolved() -> None:
    text = "Refunds take five days [2]."
    resolved = _resolve(text)
    start, end = resolved[0].spans[0]
    assert text[start:end] == "[2]"


def test_code_masking_preserves_offsets() -> None:
    """Blanking code with same-length spaces is what keeps spans valid."""
    text = "Ignore `x[1]` but not this [2]."
    resolved = _resolve(text)
    start, end = resolved[0].spans[0]
    assert text[start:end] == "[2]"


# ── serialisation ────────────────────────────────────────────


def test_a_citation_is_json_serialisable() -> None:
    """Message.blocks is a JSONB column written with the stdlib json.dumps,
    which cannot serialise a uuid.UUID — and it would blow up at flush(),
    after the answer had already streamed to the browser."""
    reg = CitationRegistry()
    reg.register([chunk("Handbook")])
    (citation,) = reg.resolve("As documented [1].")
    json.dumps(asdict(citation))  # must not raise
    assert isinstance(citation.chunk_id, str)
    assert isinstance(citation.data_source_id, str)


def test_blocks_carry_a_type_discriminator() -> None:
    reg = CitationRegistry()
    reg.register([chunk("Handbook")])
    blocks = blocks_from(reg.resolve("Cited [1]."))
    assert blocks[0]["type"] == "citation"
    assert blocks[0]["marker"] == 1


def test_a_chunk_with_no_data_source_still_resolves() -> None:
    """_attach_source_metadata hands out None when the join misses. A citation
    that can't be opened must still render, not destroy the turn."""
    reg = CitationRegistry()
    reg.register([chunk("Orphan", data_source_id=None)])
    (citation,) = reg.resolve("Per the handbook [1].")
    assert citation.data_source_id is None
    assert citation.href is None
    json.dumps(asdict(citation))


# ── deep links ───────────────────────────────────────────────


def test_a_web_source_gets_a_text_fragment_deep_link() -> None:
    reg = CitationRegistry()
    reg.register(
        [
            chunk(
                "Docs",
                source_type="url",
                uri="https://example.com/guide",
                content="Refunds are processed within five business days of the request.",
            )
        ]
    )
    (citation,) = reg.resolve("See the guide [1].")
    assert citation.href is not None
    assert citation.href.startswith("https://example.com/guide#:~:text=")
    assert "%20" in citation.href  # spaces encoded, not raw


def test_a_uri_that_already_has_a_fragment_does_not_get_a_second_hash() -> None:
    reg = CitationRegistry()
    reg.register(
        [chunk("Docs", source_type="url", uri="https://example.com/g#install", content="Run setup")]
    )
    (citation,) = reg.resolve("See [1].")
    assert citation.href is not None
    assert citation.href.count("#") == 1
    assert ":~:text=" in citation.href


def test_file_and_text_sources_get_no_precomputed_href() -> None:
    """A file needs a presigned URL minted on demand; pasted text has no
    external target at all."""
    reg = CitationRegistry()
    reg.register([chunk("Handbook.pdf", source_type="file"), chunk("Pasted", source_type="text")])
    resolved = reg.resolve("Both [1][2].")
    assert all(c.href is None for c in resolved)


def test_loc_carries_everything_the_panel_renders() -> None:
    reg = CitationRegistry()
    reg.register(
        [
            chunk(
                "Handbook",
                source_type="file",
                page=3,
                page_end=4,
                char_start=1200,
                char_end=2000,
                breadcrumb=["Chapter 2", "2.1 Refunds"],
                ordinal=7,
            )
        ]
    )
    (citation,) = reg.resolve("Per the handbook [1].")
    assert citation.loc == {
        "page": 3,
        "page_end": 4,
        "char_start": 1200,
        "char_end": 2000,
        "breadcrumb": ["Chapter 2", "2.1 Refunds"],
        "ordinal": 7,
    }


def test_a_chunk_indexed_before_2_9_has_no_char_range_rather_than_a_wrong_one() -> None:
    reg = CitationRegistry()
    reg.register([chunk("Legacy")])  # no char_start/char_end set
    (citation,) = reg.resolve("Old source [1].")
    assert citation.loc["char_start"] is None
    assert citation.loc["char_end"] is None
