"""Write the API's OpenAPI schema to `packages/shared/openapi.json`.

Run:  python -m scripts.export_openapi          (from apps/api)
Then: npm run gen -w @assistant-studio/shared   (regenerates the TS types)

The file is committed. `tests/test_openapi_snapshot.py` fails whenever the
live schema and the committed one disagree, so an API change cannot land
without the TypeScript types the web app compiles against changing with it.
That is the point: `apps/web/lib/api.ts` used to be hand-written types that
drifted silently from the Pydantic schemas.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from app import __version__

OUT = Path(__file__).resolve().parents[3] / "packages" / "shared" / "openapi.json"


def build_schema() -> dict[str, Any]:
    """The schema, normalised so it depends on code only, not on the
    environment it was exported from (the app title comes from settings)."""
    from app.main import create_app

    schema = create_app().openapi()
    schema["info"] = {"title": "Assistant Studio API", "version": __version__}
    return schema


def render(schema: dict[str, Any]) -> str:
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> None:
    OUT.write_text(render(build_schema()), encoding="utf-8", newline="\n")
    sys.stdout.write(f"wrote {OUT}\n")


if __name__ == "__main__":
    main()
