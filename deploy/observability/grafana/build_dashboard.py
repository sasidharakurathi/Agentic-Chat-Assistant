"""Builds `dashboards/assistant-studio.json` (task 6.4).

The dashboard is generated, not hand-edited: a Grafana dashboard is a few
thousand lines of JSON in which the dozen that matter (the queries) are
hard to find and easy to break. Change a panel here and run:

    python deploy/observability/grafana/build_dashboard.py

`apps/api/tests/test_metrics.py` checks that the committed JSON matches
this script's output and that every metric a panel queries is exported.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

OUT = Path(__file__).parent / "dashboards" / "assistant-studio.json"
DATASOURCE = {"type": "prometheus", "uid": "assistant-studio-prometheus"}
P = "assistant_studio_"

# (title, unit, [(expr, legend)], kind, description), in rows of three.
ROWS: list[tuple[str, list[tuple[str, str, list[tuple[str, str]], str, str]]]] = [
    (
        "Spend",
        [
            (
                "Spend per hour, by model",
                "currencyUSD",
                [(f"sum by (model) (rate({P}cost_usd_total[1h])) * 3600", "{{model}}")],
                "timeseries",
                "What the platform is spending, from the usage ledger.",
            ),
            (
                "Spend per hour, by assistant",
                "currencyUSD",
                [
                    (
                        f"topk(10, sum by (assistant) (rate({P}assistant_cost_usd_total[1h])) * 3600)",
                        "{{assistant}}",
                    )
                ],
                "timeseries",
                "The ten assistants spending the most.",
            ),
            (
                "Budgets used",
                "percentunit",
                [(f"{P}budget_used_ratio", "{{scope}} ({{period}})")],
                "bargauge",
                "Each budget against its limit. Chats are warned from 80% and refused at 100%.",
            ),
        ],
    ),
    (
        "Requests and the error budget",
        [
            (
                "Requests per second, by status",
                "reqps",
                [(f"sum by (status) (rate({P}http_requests_total[5m]))", "{{status}}")],
                "timeseries",
                "",
            ),
            (
                "Requests failing (5xx)",
                "percentunit",
                [
                    (
                        f'sum(rate({P}http_requests_total{{status="5xx"}}[5m])) '
                        f"/ sum(rate({P}http_requests_total[5m]))",
                        "5xx share",
                    )
                ],
                "timeseries",
                "The error budget: the alert fires above 5% for 10 minutes.",
            ),
            (
                "Request time, 95th percentile by route",
                "s",
                [
                    (
                        "topk(8, histogram_quantile(0.95, sum by (le, route) "
                        f"(rate({P}http_request_duration_seconds_bucket[5m]))))",
                        "{{route}}",
                    )
                ],
                "timeseries",
                "The eight slowest routes.",
            ),
        ],
    ),
    (
        "Agent turns",
        [
            (
                "Turn duration",
                "s",
                [
                    (
                        "histogram_quantile(0.5, sum by (le) "
                        f"(rate({P}turn_duration_seconds_bucket[15m])))",
                        "median",
                    ),
                    (
                        "histogram_quantile(0.95, sum by (le) "
                        f"(rate({P}turn_duration_seconds_bucket[15m])))",
                        "95th percentile",
                    ),
                ],
                "timeseries",
                "From the message arriving to the answer being saved.",
            ),
            (
                "Where a turn's time goes: tool calls, 95th percentile",
                "s",
                [
                    (
                        "histogram_quantile(0.95, sum by (le, tool) "
                        f"(rate({P}tool_duration_seconds_bucket[15m])))",
                        "{{tool}}",
                    )
                ],
                "timeseries",
                "",
            ),
            (
                "Knowledge-base search, 95th percentile",
                "s",
                [
                    (
                        "histogram_quantile(0.95, sum by (le) "
                        f"(rate({P}retrieval_duration_seconds_bucket[15m])))",
                        "retrieval",
                    )
                ],
                "timeseries",
                "Embedding the query, both searches, fusion and reranking.",
            ),
            (
                "Turns per minute, by how they ended",
                "short",
                [(f"sum by (status) (rate({P}runs_total[15m])) * 60", "{{status}}")],
                "timeseries",
                "",
            ),
            (
                "Tool calls that failed",
                "percentunit",
                [
                    (
                        f'sum by (tool) (rate({P}tool_calls_total{{status="error"}}[15m])) '
                        f"/ sum by (tool) (rate({P}tool_calls_total[15m]))",
                        "{{tool}}",
                    )
                ],
                "timeseries",
                "",
            ),
            (
                "Chat streams open, 95th percentile",
                "s",
                [
                    (
                        "histogram_quantile(0.95, sum by (le) "
                        f"(rate({P}sse_stream_duration_seconds_bucket[15m])))",
                        "stream",
                    )
                ],
                "timeseries",
                "",
            ),
        ],
    ),
    (
        "Tokens",
        [
            (
                "Tokens per minute, by model and direction",
                "short",
                [
                    (
                        f"sum by (model, direction) (rate({P}tokens_total[15m])) * 60",
                        "{{model}} {{direction}}",
                    )
                ],
                "timeseries",
                "",
            ),
        ],
    ),
    (
        "Behind the scenes",
        [
            (
                "Jobs waiting for a worker",
                "short",
                [(f"{P}queue_depth", "queue")],
                "timeseries",
                "Ingestion, eval runs and summaries.",
            ),
            (
                "Sources, by status",
                "short",
                [(f"{P}data_sources", "{{status}}")],
                "timeseries",
                "",
            ),
            (
                "MCP servers, by last check",
                "short",
                [(f"{P}mcp_servers", "{{status}}")],
                "timeseries",
                "",
            ),
            (
                "Approvals waiting",
                "short",
                [(f"{P}approvals_pending", "pending")],
                "stat",
                "Tool calls waiting for a person right now.",
            ),
            (
                "How long approvals wait, 95th percentile",
                "s",
                [
                    (
                        "histogram_quantile(0.95, sum by (le, decision) "
                        f"(rate({P}approval_wait_seconds_bucket[1h])))",
                        "{{decision}}",
                    )
                ],
                "timeseries",
                "",
            ),
            (
                "Metrics that could not be read",
                "short",
                [(f"{P}scrape_errors", "{{source}}")],
                "stat",
                "1 means that group of numbers is missing from this dashboard.",
            ),
        ],
    ),
]

WIDTH, HEIGHT, PER_ROW = 8, 8, 3


def build() -> dict[str, Any]:
    panels: list[dict[str, Any]] = []
    panel_id, y = 1, 0
    for title, row in ROWS:
        panels.append(
            {
                "id": panel_id,
                "type": "row",
                "title": title,
                "collapsed": False,
                "gridPos": {"h": 1, "w": 24, "x": 0, "y": y},
                "panels": [],
            }
        )
        panel_id += 1
        y += 1
        for n, (name, unit, queries, kind, description) in enumerate(row):
            panels.append(
                {
                    "id": panel_id,
                    "type": kind,
                    "title": name,
                    "description": description,
                    "datasource": DATASOURCE,
                    "gridPos": {
                        "h": HEIGHT,
                        "w": WIDTH,
                        "x": (n % PER_ROW) * WIDTH,
                        "y": y + (n // PER_ROW) * HEIGHT,
                    },
                    "fieldConfig": {"defaults": {"unit": unit, "min": 0}, "overrides": []},
                    "targets": [
                        {
                            "datasource": DATASOURCE,
                            "expr": expr,
                            "legendFormat": legend,
                            "refId": chr(ord("A") + i),
                        }
                        for i, (expr, legend) in enumerate(queries)
                    ],
                }
            )
            panel_id += 1
        y += ((len(row) + PER_ROW - 1) // PER_ROW) * HEIGHT
    return {
        "uid": "assistant-studio",
        "title": "Assistant Studio",
        "tags": ["assistant-studio"],
        "timezone": "browser",
        "schemaVersion": 39,
        "version": 1,
        "refresh": "30s",
        "time": {"from": "now-6h", "to": "now"},
        "panels": panels,
    }


def text() -> str:
    return json.dumps(build(), indent=2) + "\n"


if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text(), encoding="utf-8")
    print(f"wrote {OUT}")
