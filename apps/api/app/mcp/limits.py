"""Resource limits for a stdio MCP server (task 4.4).

A stdio server is someone else's program running on our hardware. These are
the ceilings it runs under, per server (`mcp_servers.sandbox`), each with a
platform maximum a builder cannot exceed:

- `memory_mb`: address space (`RLIMIT_AS`). Node and Python reserve more
  virtual memory than they use, so the default is generous.
- `cpu_seconds`: total CPU time (`RLIMIT_CPU`) over the session. A busy
  loop is killed rather than holding a core.
- `max_processes`: processes and threads (`RLIMIT_NPROC`). Stops a fork bomb.
- `max_open_files`: file descriptors (`RLIMIT_NOFILE`).
- `max_file_mb`: the largest file it may write (`RLIMIT_FSIZE`). It writes
  only into its own temporary directory anyway.
- `wall_clock_s`: how long one session may live, busy or not.
- `idle_timeout_s`: how long a session may sit unused before it is stopped.

The first five are applied by `jail` (Linux); the last two by the runner,
everywhere.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SandboxLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # The ceilings sit under what the runner's container is given in
    # docker-compose.yml (2 GB, 512 processes, a 512 MB /tmp): one server
    # asking for the maximum must not be able to take the runner, and every
    # other org's servers, down with it (task 6.3).
    memory_mb: int = Field(default=1024, ge=64, le=1536)
    cpu_seconds: int = Field(default=600, ge=5, le=7200)
    max_processes: int = Field(default=128, ge=4, le=256)
    max_open_files: int = Field(default=256, ge=16, le=4096)
    max_file_mb: int = Field(default=64, ge=1, le=256)
    wall_clock_s: int = Field(default=3600, ge=10, le=86_400)
    idle_timeout_s: int = Field(default=600, ge=10, le=3600)

    @classmethod
    def from_row(cls, sandbox: dict[str, Any] | None) -> SandboxLimits:
        """The row's overrides on top of the defaults. Unknown or out-of-range
        values stored before a rule changed fall back to the default rather
        than failing a turn."""
        out = cls()
        for key, value in (sandbox or {}).items():
            if key in cls.model_fields:
                try:
                    out = out.model_copy(
                        update=cls.model_validate({key: value}).model_dump(include={key})
                    )
                except ValueError:
                    continue
        return out


__all__ = ["SandboxLimits"]
