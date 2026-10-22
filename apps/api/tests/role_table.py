"""The threat model's list of who may call each route, written from
`ROLE_MATRIX` (Phase 7b.10), so the document and the tested table can't
drift apart. `test_role_matrix` fails when they differ; to rewrite the
list after changing the matrix, from apps/api:

    python -m tests.role_table
"""

from __future__ import annotations

from pathlib import Path

DOC = Path(__file__).resolve().parents[3] / "docs" / "THREAT_MODEL.md"
START = "<!-- role-matrix:start (written by `python -m tests.role_table`) -->"
END = "<!-- role-matrix:end -->"

#: Each level of the matrix in words, in the order the list shows them.
WHO: dict[str, str] = {
    "public": "Anyone, signed in or not",
    "signed-in": "Anyone signed in, about themselves",
    "member": "Any member of the organisation",
    "editor": "Whoever may edit the assistant: its creator, its co-editors, admins and the owner",
    "reader": (
        "The person who started the conversation; an admin or the owner, "
        "giving a reason that is recorded first"
    ),
    "chat": "Chat: every member, employees included (an assistant that answers in Chat)",
    "chat-reader": (
        "Chat, reading a conversation: the person who started it, employees included; an "
        "admin or the owner, giving a reason that is recorded first"
    ),
    "chat-starter": (
        "Chat, acting in a conversation: the person who started it, employees included"
    ),
    "starter": "The person who started the conversation, and nobody else",
    "decider": (
        "One of the assistant's approvers (admins and the owner while it names nobody), "
        "never the person who asked"
    ),
    "request": (
        "One request: the person who asked, whoever may decide it or decided it, admins "
        "and the owner"
    ),
    "admin": "Admins and the owner",
    "owner": "The owner alone",
}


def render(matrix: dict[str, str]) -> str:
    """The list, between its markers."""
    unknown = set(matrix.values()) - WHO.keys()
    assert not unknown, f"describe these levels in WHO: {sorted(unknown)}"
    parts = [START, ""]
    for level, words in WHO.items():
        routes = sorted(
            (k for k, v in matrix.items() if v == level),
            key=lambda k: (k.split(" ", 1)[1], k.split(" ", 1)[0]),
        )
        if not routes:
            continue
        parts += [f"### {words}", ""]
        parts += [f"- `{route}`" for route in routes]
        parts.append("")
    parts.append(END)
    return "\n".join(parts)


def current() -> str:
    text = DOC.read_text(encoding="utf-8")
    start, end = text.index(START), text.index(END) + len(END)
    return text[start:end]


def main() -> None:
    import sys

    # As pytest does: the test modules import each other by bare name.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from tests.test_role_matrix import ROLE_MATRIX

    text = DOC.read_text(encoding="utf-8")
    start, end = text.index(START), text.index(END) + len(END)
    DOC.write_text(text[:start] + render(ROLE_MATRIX) + text[end:], encoding="utf-8", newline="\n")
    print(f"wrote {len(ROLE_MATRIX)} routes into {DOC.name}")


if __name__ == "__main__":
    main()
