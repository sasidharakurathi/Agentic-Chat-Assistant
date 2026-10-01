"""Become a stdio MCP server, inside limits (task 4.4). POSIX only.

    python -I /path/to/jail.py '<limits json>' -- <command> [args...]

The runner starts this instead of the server itself. It sets everything
that has to be set from *inside* the process, then `exec`s the real command,
so the server is this process and inherits every restriction:

1. a new session, so the runner can kill the whole process group;
2. `umask 077`, so anything it writes is private to it;
3. root is dropped to `nobody` if the runner was started as root (it
   shouldn't be; the container runs as an unprivileged user);
4. `no_new_privs`, so no setuid binary can hand the server more rights than
   it started with;
5. resource limits (`SandboxLimits`), soft and hard, so the server cannot
   raise them again; and no core dumps, which could write its memory
   (including any secrets in its environment) to disk.

Only then does the command run. Environment and working directory are set
by the runner before this starts.

**Standalone on purpose.** It runs by file path with `python -I` (isolated:
no `PYTHONPATH`, no user site-packages) from the server's private
directory, where the `app` package is not importable, and it should not be:
nothing of the platform belongs in that process. So it imports only the
standard library and reads the limits as plain JSON (the runner writes them
from a validated `SandboxLimits`).
"""

from __future__ import annotations

import contextlib
import ctypes
import json
import os
import shutil
import sys

_MB = 1024 * 1024
_NOBODY = 65534
_PR_SET_NO_NEW_PRIVS = 38

#: limits key -> (label, RLIMIT_* name, multiplier)
_TABLE: tuple[tuple[str, str, str, int], ...] = (
    ("memory_mb", "memory", "RLIMIT_AS", _MB),
    ("cpu_seconds", "cpu time", "RLIMIT_CPU", 1),
    ("max_processes", "processes", "RLIMIT_NPROC", 1),
    ("max_open_files", "open files", "RLIMIT_NOFILE", 1),
    ("max_file_mb", "file size", "RLIMIT_FSIZE", _MB),
)


def apply_limits(limits: dict[str, int]) -> list[str]:
    """Set every limit this platform supports, soft and hard; return the
    names applied. Core dumps are always off."""
    if sys.platform == "win32":  # an `if`, so the type checker agrees on both
        return []
    import resource

    applied = []
    wanted = [
        (label, name, int(limits[key]) * scale)
        for key, label, name, scale in _TABLE
        if key in limits
    ]
    wanted.append(("core dumps", "RLIMIT_CORE", 0))
    for label, name, value in wanted:
        which = getattr(resource, name, None)
        if which is None:
            continue
        _soft, hard = resource.getrlimit(which)
        # A hard limit can only be lowered, never raised.
        limit = value if hard == resource.RLIM_INFINITY else min(value, hard)
        resource.setrlimit(which, (limit, limit))
        applied.append(label)
    return applied


def drop_privileges() -> None:
    if sys.platform == "win32":
        return
    if os.getuid() == 0:
        os.setgroups([])
        os.setgid(_NOBODY)
        os.setuid(_NOBODY)


def no_new_privileges() -> bool:
    """Linux: forbid gaining privileges through exec. False where unsupported."""
    if not sys.platform.startswith("linux"):
        return False
    libc = ctypes.CDLL(None, use_errno=True)
    return bool(libc.prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) == 0)


def main(argv: list[str]) -> int:
    if sys.platform == "win32":
        print("the MCP jail runs on POSIX systems only", file=sys.stderr)
        return 2
    if len(argv) < 4 or argv[2] != "--":  # noqa: PLR2004 — argv shape documented above
        print("usage: jail.py '<limits json>' -- <command> [args...]", file=sys.stderr)
        return 2
    limits = {str(k): int(v) for k, v in json.loads(argv[1]).items()}
    command = argv[3:]

    with contextlib.suppress(PermissionError):  # already a session leader
        os.setsid()
    os.umask(0o077)
    drop_privileges()
    if sys.platform.startswith("linux") and not no_new_privileges():
        # Its result used to be ignored: the server then started without the
        # one guarantee that stops it regaining privileges through exec.
        print("the jail could not set no_new_privs; not starting", file=sys.stderr)
        return 3
    apply_limits(limits)

    executable = shutil.which(command[0])
    if executable is None:
        print(f"command not found: {command[0]}", file=sys.stderr)
        return 127
    os.execv(executable, [executable, *command[1:]])
    return 0  # pragma: no cover — execv does not return


if __name__ == "__main__":
    sys.exit(main(sys.argv))
