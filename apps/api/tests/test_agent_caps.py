from __future__ import annotations

import pytest
from app.agent.caps import ALL_CAPS


async def _run(name: str, args: dict) -> str:
    result = await ALL_CAPS[name].handler(args)
    return result["content"][0]["text"]


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        ("2 + 3", "= 5.0"),
        ("10 / 4", "= 2.5"),
        ("2 ** 10", "= 1024.0"),
        ("-(3 + 4) * 2", "= -14.0"),
    ],
)
async def test_calculator_ok(expr: str, expected: str) -> None:
    assert expected in await _run("calculator", {"expression": expr})


@pytest.mark.parametrize("expr", ["", "__import__('os')", "a + b", "1 +", "2 ** 999999"])
async def test_calculator_rejects_bad_input(expr: str) -> None:
    assert "error" in await _run("calculator", {"expression": expr})


async def test_datetime_utc_and_zone() -> None:
    assert "UTC now:" in await _run("datetime", {})
    assert "Asia/Kolkata:" in await _run("datetime", {"tz": "Asia/Kolkata"})
    assert "error" in await _run("datetime", {"tz": "Mars/Olympus"})


# ── failures must be flagged as failures ─────────────────────
#
# Regression. Every cap signalled refusals by returning the string
# "blocked: ..." or "error: ..." in an otherwise ordinary successful result.
# The SSE `tool_result` therefore carried status="success", so the chat UI
# rendered a refused query exactly like one that ran — and the model was told
# its statement succeeded. `is_error` is the SDK's flag for this.


async def test_a_refused_capability_sets_is_error() -> None:
    result = await ALL_CAPS["calculator"].handler({"expression": "__import__('os')"})
    assert result["is_error"] is True
    assert result["content"][0]["text"].startswith("error:")


async def test_a_successful_capability_does_not() -> None:
    result = await ALL_CAPS["calculator"].handler({"expression": "2 + 3"})
    assert not result.get("is_error")


async def test_every_error_string_in_the_cap_modules_is_flagged() -> None:
    """A guard against the obvious regression: someone adds a new failure
    path with `_text("error: ...")` and it silently reports success again."""
    import inspect
    import re

    from app.agent import caps, caps_mongo, caps_rag, caps_sql

    offenders: list[str] = []
    for mod in (caps, caps_mongo, caps_rag, caps_sql):
        for lineno, line in enumerate(inspect.getsource(mod).splitlines(), start=1):
            if re.search(r'_text\(\s*f?"(error|blocked):', line):
                offenders.append(f"{mod.__name__}:{lineno}: {line.strip()}")
    assert offenders == []
