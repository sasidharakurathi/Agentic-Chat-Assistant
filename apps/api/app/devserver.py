"""The development server: one fresh uvicorn process per code change.

`scripts/dev-api.ps1` runs `watchfiles app.devserver.main apps/api/app`.
watchfiles starts `main` in a new Python process and, whenever a file under
the watched path changes, stops that process and starts another.

Why not `uvicorn --reload`? On Windows it cannot serve the real agent
driver, and there is no event loop that fixes both of its problems:

- The Claude Agent SDK starts its bundled CLI as a subprocess. On Windows
  only the Proactor event loop can start subprocesses, and under `--reload`
  uvicorn picks the Selector loop, so every real-driver turn failed with
  "Failed to start Claude Code".
- Forcing the Proactor loop (`--loop …`) works until the first reload.
  uvicorn's reloader opens the listening socket once and hands it to each
  new worker process, and a Windows socket can be tied to only one I/O
  completion port for its whole life. The first worker's loop claims it;
  every worker after a reload fails with `WinError 87` ("Accept failed on a
  socket") and the API stops answering.

Restarting the whole process sidesteps both: each run binds its own socket
and uses the platform's default loop, which on Windows is Proactor.
"""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "app.main:app",
        # Listens on the LAN, as dev-api.ps1 always has (the web app and phones reach it).
        host=os.environ.get("API_HOST", "0.0.0.0"),
        port=int(os.environ.get("API_PORT", "8000")),
    )


if __name__ == "__main__":
    main()
