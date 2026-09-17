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
