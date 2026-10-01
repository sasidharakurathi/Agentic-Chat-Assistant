"""Prometheus metrics (task 6.4, plan §12): a small registry and the text
format `/metrics` answers in.

No client library: what is needed is counters, gauges and histograms with
labels, rendered as text. That is a hundred lines, and one fewer dependency
to audit.

Two kinds of metric live here:

- **Observed in this process**, as things happen: request and stream
  durations, turn and tool durations, retrieval time, how long an approval
  waited. Held in memory, so they reset on restart (Prometheus expects
  that of counters) and cover the API process only.
- **Read at scrape time** from the database and Redis
  (`observability/collect.py`): spend and tokens by model, runs, tool
  calls, sources, MCP servers, budgets, the queue. Those are true across
  every process and every restart, because the ledger is.

Label values must be bounded: a route template, never a path; a tool's
name, never its input.
"""

from __future__ import annotations

import math
import threading
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

Labels = tuple[str, ...]

#: Seconds. Fine-grained where requests live, stretched out to the minutes
#: a turn with tools can take.
DEFAULT_BUCKETS = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0)


def escape(value: object) -> str:
    """A label value, quoted the way the text format requires."""
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _labels(names: Sequence[str], values: Sequence[object], extra: str = "") -> str:
    pairs = [f'{n}="{escape(v)}"' for n, v in zip(names, values, strict=True)]
    if extra:
        pairs.append(extra)
    return "{" + ",".join(pairs) + "}" if pairs else ""


def _number(value: float) -> str:
    if math.isinf(value):
        return "+Inf" if value > 0 else "-Inf"
    return repr(float(value)) if value != int(value) else str(int(value))


@dataclass
class Sample:
    """One line of output that was read rather than observed: a name,
    its label values and a number."""

    name: str
    help: str
    kind: str  # "counter" | "gauge"
    label_names: tuple[str, ...]
    values: list[tuple[Labels, float]] = field(default_factory=list)

    def render(self) -> list[str]:
        lines = [f"# HELP {self.name} {self.help}", f"# TYPE {self.name} {self.kind}"]
        for labels, value in sorted(self.values):
            lines.append(f"{self.name}{_labels(self.label_names, labels)} {_number(value)}")
        return lines


class _Metric:
    kind = ""

    def __init__(self, name: str, help: str, label_names: Sequence[str] = ()) -> None:
        self.name = name
        self.help = help
        self.label_names = tuple(label_names)
        self._lock = threading.Lock()

    def _key(self, labels: Sequence[object]) -> Labels:
        if len(labels) != len(self.label_names):
            raise ValueError(f"{self.name} takes labels {self.label_names}, got {tuple(labels)}")
        return tuple(str(v) for v in labels)

    def _head(self) -> list[str]:
        return [f"# HELP {self.name} {self.help}", f"# TYPE {self.name} {self.kind}"]


class Counter(_Metric):
    kind = "counter"

    def __init__(self, name: str, help: str, label_names: Sequence[str] = ()) -> None:
        super().__init__(name, help, label_names)
        self._values: dict[Labels, float] = {}

    def inc(self, *labels: object, by: float = 1.0) -> None:
        key = self._key(labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + by

    def value(self, *labels: object) -> float:
        return self._values.get(self._key(labels), 0.0)

    def render(self) -> list[str]:
        lines = self._head()
        for labels, value in sorted(self._values.items()):
            lines.append(f"{self.name}{_labels(self.label_names, labels)} {_number(value)}")
        return lines

    def reset(self) -> None:
        self._values.clear()


class Histogram(_Metric):
    kind = "histogram"

    def __init__(
        self,
        name: str,
        help: str,
        label_names: Sequence[str] = (),
        buckets: Sequence[float] = DEFAULT_BUCKETS,
    ) -> None:
        super().__init__(name, help, label_names)
        self.buckets = tuple(sorted(buckets))
        #: label values -> (count per bucket, sum, count)
        self._series: dict[Labels, tuple[list[int], float, int]] = {}

    def observe(self, value: float, *labels: object) -> None:
        key = self._key(labels)
        with self._lock:
            counts, total, n = self._series.get(key, ([0] * len(self.buckets), 0.0, 0))
            for i, bound in enumerate(self.buckets):
                if value <= bound:
                    counts[i] += 1
            self._series[key] = (counts, total + value, n + 1)

    def count(self, *labels: object) -> int:
        return self._series.get(self._key(labels), ([], 0.0, 0))[2]

    def render(self) -> list[str]:
        lines = self._head()
        for labels, (counts, total, n) in sorted(self._series.items()):
            for bound, count in zip(self.buckets, counts, strict=True):
                le = f'le="{_number(bound)}"'
                lines.append(f"{self.name}_bucket{_labels(self.label_names, labels, le)} {count}")
            inf = 'le="+Inf"'
            lines.append(f"{self.name}_bucket{_labels(self.label_names, labels, inf)} {n}")
            lines.append(f"{self.name}_sum{_labels(self.label_names, labels)} {_number(total)}")
            lines.append(f"{self.name}_count{_labels(self.label_names, labels)} {n}")
        return lines

    def reset(self) -> None:
        self._series.clear()


# ── what this process observes ───────────────────────────────

HTTP_REQUESTS = Counter(
    "assistant_studio_http_requests_total",
    "Requests answered, by method, route and status class.",
    ("method", "route", "status"),
)
HTTP_DURATION = Histogram(
    "assistant_studio_http_request_duration_seconds",
    "How long a request took to answer (a chat stream: until it started).",
    ("method", "route"),
)
STREAM_DURATION = Histogram(
    "assistant_studio_sse_stream_duration_seconds",
    "How long a chat stream stayed open, first byte to last.",
    (),
    buckets=(0.5, 1, 2.5, 5, 10, 20, 30, 60, 120, 300, 600),
)
TURN_DURATION = Histogram(
    "assistant_studio_turn_duration_seconds",
    "How long an agent turn took, by how it ended.",
    ("status",),
    buckets=(0.5, 1, 2.5, 5, 10, 20, 30, 60, 120, 300, 600),
)
TOOL_DURATION = Histogram(
    "assistant_studio_tool_duration_seconds",
    "How long a tool call took, by tool and outcome.",
    ("tool", "status"),
)
RETRIEVAL_DURATION = Histogram(
    "assistant_studio_retrieval_duration_seconds",
    "How long one knowledge-base search took (embed, search, fuse, rerank).",
    (),
)
APPROVAL_WAIT = Histogram(
    "assistant_studio_approval_wait_seconds",
    "How long a tool call waited for a person, by what they decided.",
    ("decision",),
    buckets=(1, 5, 15, 30, 60, 120, 300, 600),
)

QUERY_CACHE = Counter(
    "assistant_studio_query_cache_total",
    "Knowledge-base searches whose query embedding was cached (hit) or computed (miss).",
    ("result",),
)

OBSERVED: tuple[Counter | Histogram, ...] = (
    QUERY_CACHE,
    HTTP_REQUESTS,
    HTTP_DURATION,
    STREAM_DURATION,
    TURN_DURATION,
    TOOL_DURATION,
    RETRIEVAL_DURATION,
    APPROVAL_WAIT,
)


def status_class(code: int) -> str:
    """`2xx`, `4xx`, `5xx`: the label, so there is one series per class and
    not one per code."""
    return f"{code // 100}xx"


def short_tool(name: str) -> str:
    """`mcp__caps__sql_query` -> `sql_query`. The server is a separate
    dimension elsewhere; here it would only multiply the series."""
    return name.rsplit("__", 1)[-1]


def render(samples: Iterable[Sample] = ()) -> str:
    """The whole page: what this process observed, then what was read."""
    lines: list[str] = []
    for metric in OBSERVED:
        lines += metric.render()
    for sample in samples:
        lines += sample.render()
    return "\n".join(lines) + "\n"


def reset() -> None:
    """For tests: forget everything observed."""
    for metric in OBSERVED:
        metric.reset()


__all__ = [
    "APPROVAL_WAIT",
    "DEFAULT_BUCKETS",
    "HTTP_DURATION",
    "HTTP_REQUESTS",
    "OBSERVED",
    "QUERY_CACHE",
    "RETRIEVAL_DURATION",
    "STREAM_DURATION",
    "TOOL_DURATION",
    "TURN_DURATION",
    "Counter",
    "Histogram",
    "Sample",
    "escape",
    "render",
    "reset",
    "short_tool",
    "status_class",
]
