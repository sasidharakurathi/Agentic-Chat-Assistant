"""Concurrent chats against a running API (task 6.5, plan §11.1).

Signs in, makes one assistant, then holds `--concurrency` conversations
going at once until `--turns` messages have been answered. For each turn it
records how long the first token took and how long the whole answer took,
and at the end prints the percentiles the PRD sets targets for
(p95 first token < 3.5 s, p95 full answer < 12 s) and what went wrong.

    python -m scripts.loadtest --email you@example.com --concurrency 16 --turns 200

Run it against the **fake driver** (`AGENT_DRIVER=fake`): it costs nothing
and measures the platform itself (the database, the stream, the turn's
bookkeeping, the concurrency limit). `AGENT_FAKE_DELAY_MS` makes each fake
turn take as long as a real one, which is what fills the turn slots and the
connection pool. Against a real model the same script measures the model
too, and spends money on every turn.

Rate limits apply to it like to anyone: for a load test, start the API with
`RATE_LIMIT_ENABLED=0`, or the results are mostly 429s (they are counted).

`--register` makes a throwaway account (a random address and a random
password that is never shown or stored) instead of signing in. Use it
against a database made for the test, which is also where a few thousand
test conversations belong:

    python -m scripts.loadtest --register --concurrency 8 32 64 --turns 300

While it runs it reads `/metrics` once a second and reports the most turns
it saw running and waiting, and the most database connections in use, so a
result says what was full and not only how slow it was. If the API needs a
token for that page, put it in `LOADTEST_METRICS_TOKEN`.

No load-testing framework (the plan named Locust): this is one file on
httpx, which the API already depends on. The password is read from
`LOADTEST_PASSWORD` or asked for, never taken as an argument (it would sit
in the shell history).
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import math
import os
import re
import secrets
import statistics
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import httpx

API = "/api/v1"


@dataclass
class Turn:
    ok: bool
    first_token_s: float | None = None
    total_s: float = 0.0
    #: "ok", an SSE error code ("busy", "budget_exceeded", ...), or "http_429".
    outcome: str = "ok"


@dataclass
class Report:
    concurrency: int
    turns: list[Turn] = field(default_factory=list)
    wall_s: float = 0.0
    #: The highest value each watched gauge reached, from `/metrics`.
    peaks: dict[str, float] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        good = [t for t in self.turns if t.ok]
        first = [t.first_token_s for t in good if t.first_token_s is not None]
        total = [t.total_s for t in good]
        return {
            "concurrency": self.concurrency,
            "turns": len(self.turns),
            "ok": len(good),
            "failed": len(self.turns) - len(good),
            "outcomes": dict(Counter(t.outcome for t in self.turns)),
            "wall_s": round(self.wall_s, 2),
            "turns_per_s": round(len(good) / self.wall_s, 2) if self.wall_s else 0.0,
            "first_token_s": percentiles(first),
            "full_answer_s": percentiles(total),
            "peaks": self.peaks,
        }


def percentiles(values: list[float]) -> dict[str, float] | None:
    """p50, p95, p99 and the worst, or None with nothing to measure."""
    if not values:
        return None
    ordered = sorted(values)

    def at(q: float) -> float:
        # Nearest rank: the smallest value that q of the samples are at or below.
        rank = max(1, min(len(ordered), math.ceil(q * len(ordered))))
        return round(ordered[rank - 1], 3)

    return {
        "p50": round(statistics.median(ordered), 3),
        "p95": at(0.95),
        "p99": at(0.99),
        "max": round(ordered[-1], 3),
    }


#: The gauges worth watching during a run: `name{labels}` as `/metrics`
#: prints it -> the key it is reported under.
WATCHED = {
    'assistant_studio_turns{state="running"}': "turns_running",
    'assistant_studio_turns{state="waiting"}': "turns_waiting",
    'assistant_studio_db_pool_connections{state="in_use"}': "db_connections_in_use",
    "assistant_studio_db_pool_limit": "db_connections_limit",
    "assistant_studio_turn_slots": "turn_slots",
}
_SAMPLE = re.compile(r"^(\S+?(?:\{[^}]*\})?) (\S+)$", re.M)


def read_gauges(text: str) -> dict[str, float]:
    """The watched gauges out of a `/metrics` page."""
    found = {name: float(value) for name, value in _SAMPLE.findall(text) if name in WATCHED}
    return {WATCHED[name]: value for name, value in found.items()}


async def watch(client: httpx.AsyncClient, peaks: dict[str, float], every_s: float = 1.0) -> None:
    """Until cancelled: keep the highest value each gauge reaches. These
    gauges are read from the API's memory on every scrape (the cache covers
    only the database totals), so a short peak is not hidden."""
    token = os.environ.get("LOADTEST_METRICS_TOKEN", "")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    in_use: list[float] = []
    while True:
        try:
            page = await client.get("/metrics", headers=headers, timeout=5.0)
            if page.status_code == httpx.codes.OK:
                gauges = read_gauges(page.text)
                for key, value in gauges.items():
                    peaks[key] = max(peaks.get(key, 0.0), value)
                # The peak is the moment every chat started at once; the
                # median is what the run looked like the rest of the time.
                in_use.append(gauges.get("db_connections_in_use", 0.0))
                peaks["db_connections_typical"] = statistics.median(in_use)
        except httpx.HTTPError:
            pass  # a slow scrape under load is not the test's failure
        await asyncio.sleep(every_s)


async def one_turn(
    client: httpx.AsyncClient, headers: dict[str, str], conv: str, text: str
) -> Turn:
    started = time.perf_counter()
    first: float | None = None
    outcome = "ok"
    try:
        async with client.stream(
            "POST", f"{API}/conversations/{conv}/messages", json={"text": text}, headers=headers
        ) as response:
            if response.status_code != httpx.codes.OK:
                await response.aread()
                return Turn(
                    False, None, time.perf_counter() - started, f"http_{response.status_code}"
                )
            done = False
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                event = json.loads(line[6:])
                kind = event.get("type")
                if kind == "token" and first is None:
                    first = time.perf_counter() - started
                elif kind == "error":
                    outcome = str(event.get("code", "error"))
                elif kind == "done":
                    done = True
            if outcome == "ok" and not done:
                outcome = "no_done_event"
    except httpx.HTTPError as exc:
        outcome = type(exc).__name__
    return Turn(outcome == "ok", first, time.perf_counter() - started, outcome)


async def run(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    assistant_id: str,
    *,
    concurrency: int,
    turns: int,
    message: str,
) -> Report:
    """`concurrency` conversations, each sending messages one after another,
    until `turns` have been sent in total."""
    report = Report(concurrency=concurrency)
    remaining = turns
    lock = asyncio.Lock()

    async def chatter(n: int) -> None:
        nonlocal remaining
        try:
            made = await client.post(
                f"{API}/assistants/{assistant_id}/conversations", json={}, headers=headers
            )
            made.raise_for_status()
        except httpx.HTTPError as exc:
            # A server too loaded to open a conversation is a result, not a
            # reason to lose the run's other numbers.
            report.turns.append(Turn(False, outcome=f"no_conversation_{type(exc).__name__}"))
            return
        conv = made.json()["id"]
        while True:
            async with lock:
                if remaining <= 0:
                    return
                remaining -= 1
                k = turns - remaining
            report.turns.append(await one_turn(client, headers, conv, f"{message} ({n}.{k})"))

    watcher = asyncio.create_task(watch(client, report.peaks))
    started = time.perf_counter()
    try:
        await asyncio.gather(*(chatter(n) for n in range(concurrency)))
    finally:
        report.wall_s = time.perf_counter() - started
        watcher.cancel()
    return report


async def sign_in(
    client: httpx.AsyncClient, email: str, password: str, *, register: bool = False
) -> dict[str, str]:
    if register:
        body = {"email": email, "password": password, "name": "Load test"}
        r = await client.post(f"{API}/auth/register", json=body)
        if r.status_code != httpx.codes.CREATED:
            raise SystemExit(f"Could not register {email}: HTTP {r.status_code}")
    else:
        r = await client.post(f"{API}/auth/login", json={"email": email, "password": password})
        if r.status_code != httpx.codes.OK:
            raise SystemExit(f"Could not sign in as {email}: HTTP {r.status_code}")
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    me = await client.get(f"{API}/auth/me", headers=headers)
    me.raise_for_status()
    return {**headers, "X-Org-Id": me.json()["memberships"][0]["org_id"]}


def show(summary: dict[str, Any]) -> str:
    lines = [
        f"{summary['turns']} turns at concurrency {summary['concurrency']}: "
        f"{summary['ok']} answered, {summary['failed']} failed, "
        f"{summary['turns_per_s']} turns/s over {summary['wall_s']} s",
    ]
    for key, label, target in (
        ("first_token_s", "first token", 3.5),
        ("full_answer_s", "full answer", 12.0),
    ):
        p = summary[key]
        if p is None:
            lines.append(f"  {label:<12} nothing to measure")
            continue
        verdict = "within" if p["p95"] < target else "OVER"
        lines.append(
            f"  {label:<12} p50 {p['p50']:.3f} s   p95 {p['p95']:.3f} s   p99 {p['p99']:.3f} s   "
            f"max {p['max']:.3f} s   ({verdict} the {target:g} s target)"
        )
    peaks = summary.get("peaks") or {}
    if peaks:
        lines.append(
            "  at most      "
            f"{peaks.get('turns_running', 0):g} of {peaks.get('turn_slots', 0):g} turn slots busy, "
            f"{peaks.get('turns_waiting', 0):g} turns waiting, "
            f"{peaks.get('db_connections_in_use', 0):g} of "
            f"{peaks.get('db_connections_limit', 0):g} database connections in use "
            f"(typically {peaks.get('db_connections_typical', 0):g})"
        )
    failures = {k: v for k, v in summary["outcomes"].items() if k != "ok"}
    if failures:
        lines.append("  failures: " + ", ".join(f"{k} x{v}" for k, v in sorted(failures.items())))
    return "\n".join(lines)


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--base-url", default="http://localhost:8000")
    who = parser.add_mutually_exclusive_group(required=True)
    who.add_argument("--email", help="sign in as this account")
    who.add_argument(
        "--register", action="store_true", help="make a throwaway account for this run"
    )
    parser.add_argument("--concurrency", type=int, nargs="+", default=[8])
    parser.add_argument("--turns", type=int, default=100, help="messages per concurrency level")
    parser.add_argument("--message", default="hello there")
    parser.add_argument("--json", action="store_true", help="print the summaries as JSON")
    args = parser.parse_args(argv)
    if args.register:
        email = f"loadtest-{secrets.token_hex(4)}@example.com"
        password = secrets.token_urlsafe(24)
    else:
        email = args.email
        password = os.environ.get("LOADTEST_PASSWORD") or getpass.getpass(f"Password for {email}: ")

    limits = httpx.Limits(max_connections=max(args.concurrency) + 10)
    timeout = httpx.Timeout(120.0, connect=10.0)
    async with httpx.AsyncClient(base_url=args.base_url, limits=limits, timeout=timeout) as client:
        headers = await sign_in(client, email, password, register=args.register)
        made = await client.post(
            f"{API}/assistants", json={"name": "Load test (safe to delete)"}, headers=headers
        )
        made.raise_for_status()
        assistant_id = made.json()["id"]
        summaries = []
        try:
            for level in args.concurrency:
                report = await run(
                    client,
                    headers,
                    assistant_id,
                    concurrency=level,
                    turns=args.turns,
                    message=args.message,
                )
                summaries.append(report.summary())
                if not args.json:
                    print(show(summaries[-1]), flush=True)
        finally:
            # Its conversations go with it.
            await client.delete(f"{API}/assistants/{assistant_id}", headers=headers)
    if args.json:
        print(json.dumps(summaries, indent=2))
    return 0 if all(s["failed"] == 0 for s in summaries) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
