"""Contract test: the committed OpenAPI schema is the live one (plan §11.1).

`packages/shared/openapi.json` is what the web app's TypeScript types are
generated from. If an endpoint or schema changes without re-exporting it, the
frontend is compiling against an API that no longer exists, which is exactly
how the hand-written `api.ts` drifted before. Fix a failure here with:

    python -m scripts.export_openapi          (from apps/api)
    npm run gen -w @assistant-studio/shared
"""

from __future__ import annotations

import json

from scripts.export_openapi import OUT, build_schema, render


def test_the_committed_schema_matches_the_live_api() -> None:
    committed = OUT.read_text(encoding="utf-8")
    live = render(build_schema())
    if committed != live:
        before = json.loads(committed)
        after = json.loads(live)
        added = sorted(set(after["paths"]) - set(before["paths"]))
        removed = sorted(set(before["paths"]) - set(after["paths"]))
        changed = sorted(
            name
            for name in set(before["components"]["schemas"]) & set(after["components"]["schemas"])
            if before["components"]["schemas"][name] != after["components"]["schemas"][name]
        )
        raise AssertionError(
            "packages/shared/openapi.json is stale. Run `python -m scripts.export_openapi` "
            "(apps/api) then `npm run gen -w @assistant-studio/shared`.\n"
            f"paths added: {added}\npaths removed: {removed}\nschemas changed: {changed}"
        )


def test_the_export_is_deterministic() -> None:
    """A snapshot of a non-deterministic export would fail at random."""
    assert render(build_schema()) == render(build_schema())
