"""Mapping ``[n]`` markers in an answer back to the chunks they came from.

The problem this solves isn't obvious until you look at the numbering.
``kb_search`` (2.8) numbers its own results ``[1]..[8]`` on every call. An
agent that searches twice therefore produces **two** ``[1]``s, and at
finalize there is no way to tell which one the model meant. Worse, the SDK
session is *resumed* across turns (see ``chat.run_message`` ->
``options.resume``), so turn 2's context still contains turn 1's numbered
results — restarting at ``[1]`` each turn makes a stale marker resolve to a
different document entirely. That is the worst failure this feature has: a
confident citation pointing at the wrong source, with no error anywhere.

So markers are assigned by a registry whose lifetime matches the *model's
context* — the conversation — not the turn:

* numbers are handed out globally and **never reused** within a conversation;
* the same chunk retrieved again keeps the number it already had;
* a marker the model invents, or one pointing at a chunk from an older turn
  that this registry no longer holds, resolves to nothing rather than to
  whatever happens to be sitting at that index now.

Resolution happens once, at finalize, against the exact string that gets
persisted as the message content — so the char offsets recorded here stay
valid for the UI that renders them.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import quote

from app.rag.retrieve import RetrievedChunk

# How much chunk text to carry into the sources panel. The full chunk can be
# a couple of thousand characters; the panel wants a readable preview.
SNIPPET_CHARS = 400

# Words used to build a browser text fragment for web sources. Too few and it
# matches the wrong paragraph; too many and any whitespace normalisation the
# page did will stop it matching at all.
_FRAGMENT_WORDS = 8

# Cap on how many chunk->marker pairs we carry forward on a conversation.
# Bounded so a long-running conversation can't grow the row without limit;
# the oldest entries falling off only means an old chunk gets a fresh number
# if it's retrieved again, never that a live marker gets reassigned.
MAX_TRACKED = 250

# Bracketed spans that are NOT citations. Fenced blocks and inline code are
# blanked (with same-length spaces, so every offset stays valid) before
# scanning — otherwise `rows = data[1]` in a code sample parses as a citation
# and the sources panel claims two sources the answer never used.
_CODE = re.compile(r"```.*?```|~~~.*?~~~|`[^`\n]*`", re.S)

# One bracketed group: [3], [2, 3], [2; 3].
_GROUP = r"\[\s*\d{1,3}(?:\s*[,;]\s*\d{1,3})*\s*\]"

# Citations come in *runs* of adjacent groups — models write "[1][2]" as
# readily as "[1], [2]" — and the guard has to apply to the run, not to each
# group. Checking each group independently can't tell "[1][2]" (two
# citations) from "a[0][1]" (chained indexing), because in both cases the
# second `[` follows a `]`. Anchoring at the run's start distinguishes them:
# `a[0][1]` starts after a word character and is rejected whole.
#
# The lookbehind errs toward dropping a real citation written as `policy[1]`
# rather than inventing one from `items[1]`. That asymmetry is deliberate: a
# missed citation renders as inert text, a false one fabricates attribution.
# The system prompt asks the model to leave a space before the bracket.
_RUN = re.compile(rf"(?<![\w\]\)])(?:{_GROUP})+")
_GROUP_RE = re.compile(_GROUP)


@dataclass
class Citation:
    """One resolved ``[n]`` — everything the SSE event and the persisted
    block need. Ids are ``str``, not ``uuid.UUID``: this lands in a JSONB
    column via the stdlib ``json.dumps``, which cannot serialise a UUID and
    would raise at ``flush()`` *after* the answer had already streamed."""

    marker: int
    chunk_id: str
    document_id: str
    data_source_id: str | None
    title: str
    source_type: str | None
    uri: str | None
    snippet: str
    score: float
    loc: dict[str, Any] = field(default_factory=dict)
    # A ready-to-open address, when one exists at all. Only web sources get
    # one here; a file needs a short-lived presigned URL minted on demand
    # (the UI asks for it on click), and pasted text has no external target.
    href: str | None = None
    # Where this marker appears in the final message content, as [start, end)
    # char ranges. The UI splits the string by these instead of re-deriving
    # markers in TypeScript — which would mean reimplementing the code-fence
    # masking above and disagreeing with the backend the first time it drifted.
    spans: list[list[int]] = field(default_factory=list)


def _snippet(text: str) -> str:
    text = " ".join(text.split())
    if len(text) <= SNIPPET_CHARS:
        return text
    return text[:SNIPPET_CHARS].rsplit(" ", 1)[0] + "…"


def _text_fragment_href(uri: str, content: str) -> str | None:
    """A ``#:~:text=`` deep link into a web page.

    Chrome/Edge scroll to and highlight the first match. It only matches on
    word boundaries, which is why the chunker snaps chunk starts forward to
    one — a fragment beginning mid-word silently highlights nothing at all.
    """
    words = " ".join(content.split())
    if not words:
        return None
    fragment = " ".join(words.split(" ")[:_FRAGMENT_WORDS])
    # `-`, `&` and `,` are text-fragment grammar characters; encoding the
    # whole thing is cheaper than reasoning about which ones bite per string.
    encoded = quote(fragment, safe="")
    # A user-supplied URL may already carry a fragment (the create schema
    # accepts one verbatim), in which case the directive appends to it rather
    # than starting a second `#`.
    joiner = ":~:text=" if "#" in uri else "#:~:text="
    return f"{uri}{joiner}{encoded}"


def _href_for(chunk: RetrievedChunk) -> str | None:
    if chunk.source_type == "url" and chunk.uri:
        return _text_fragment_href(chunk.uri, chunk.content)
    return None


class CitationRegistry:
    """Hands out stable ``[n]`` markers and maps them back afterwards."""

    def __init__(self, state: dict[str, Any] | None = None) -> None:
        state = state or {}
        raw = state.get("markers") or {}
        self._markers: dict[str, int] = {
            str(k): int(v) for k, v in raw.items() if isinstance(v, int | str) and str(v).isdigit()
        }
        self._next: int = int(state.get("next") or 1)
        if self._markers:
            # Never hand out a number that is still live in the model's
            # context, even if a malformed seed said otherwise.
            self._next = max(self._next, max(self._markers.values()) + 1)
        self._entries: dict[int, RetrievedChunk] = {}

    def register(self, chunks: list[RetrievedChunk]) -> list[int]:
        """Assign (or recall) a marker for each chunk, in order.

        Not async and never awaits, so it stays atomic under the SDK's
        parallel tool calls: two handlers can only interleave at an await
        point, and there isn't one here.
        """
        out: list[int] = []
        for chunk in chunks:
            key = str(chunk.chunk_id)
            marker = self._markers.get(key)
            if marker is None:
                marker = self._next
                self._next += 1
                self._markers[key] = marker
            self._entries[marker] = chunk
            out.append(marker)
        return out

    def resolve(self, text: str) -> list[Citation]:
        """Every registered chunk the answer actually cited, first use first.

        ``text`` must be the exact string that gets persisted as the message
        content — the recorded spans are offsets into it.
        """
        masked = _CODE.sub(lambda m: " " * len(m.group(0)), text)
        spans: dict[int, list[list[int]]] = {}
        order: list[int] = []
        for run in _RUN.finditer(masked):
            for group in _GROUP_RE.finditer(run.group(0)):
                start = run.start() + group.start()
                end = run.start() + group.end()
                for raw_part in re.split(r"[,;]", group.group(0).strip("[]")):
                    part = raw_part.strip()
                    if not part.isdigit():
                        continue
                    marker = int(part)
                    if marker not in self._entries:
                        # Hallucinated, or from a turn this registry no longer
                        # holds. Dropped on purpose — resolving it to whatever
                        # sits at that index now is how you get a wrong
                        # citation, which is worse than showing none.
                        continue
                    if marker not in spans:
                        spans[marker] = []
                        order.append(marker)
                    spans[marker].append([start, end])
        return [self._citation(m, spans[m]) for m in order]

    def _citation(self, marker: int, spans: list[list[int]]) -> Citation:
        chunk = self._entries[marker]
        return Citation(
            marker=marker,
            chunk_id=str(chunk.chunk_id),
            document_id=str(chunk.document_id),
            data_source_id=str(chunk.data_source_id) if chunk.data_source_id else None,
            title=chunk.title,
            source_type=chunk.source_type,
            uri=chunk.uri,
            snippet=_snippet(chunk.content),
            score=round(float(chunk.score), 4),
            loc={
                "page": chunk.page,
                "page_end": chunk.page_end,
                "char_start": chunk.char_start,
                "char_end": chunk.char_end,
                "breadcrumb": list(chunk.breadcrumb or []),
                "ordinal": chunk.ordinal,
            },
            href=_href_for(chunk),
            spans=spans,
        )

    def dump(self) -> dict[str, Any]:
        """The state to carry onto the next turn of this conversation.

        Deliberately the full *registered* map, not just what was cited:
        ``resolve`` drops uncited registrations, so seeding from the persisted
        citations alone would under-count and re-issue numbers that are still
        live in the model's resumed context.
        """
        markers = self._markers
        if len(markers) > MAX_TRACKED:
            keep = sorted(markers.items(), key=lambda kv: kv[1])[-MAX_TRACKED:]
            markers = dict(keep)
        return {"markers": markers, "next": self._next}


def blocks_from(citations: list[Citation]) -> list[dict[str, Any]]:
    """Citations as typed ``message.blocks`` entries."""
    return [{"type": "citation", **asdict(c)} for c in citations]


__all__ = ["MAX_TRACKED", "SNIPPET_CHARS", "Citation", "CitationRegistry", "blocks_from"]
