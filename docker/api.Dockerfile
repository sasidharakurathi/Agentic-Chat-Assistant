# syntax=docker/dockerfile:1
# Build context = repo root.

# ── builder ──────────────────────────────────────────────────
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /build
COPY apps/api/pyproject.toml apps/api/README.md apps/api/constraints.txt ./apps/api/
COPY apps/api/app ./apps/api/app
# --extra-index-url pulls the CPU-only torch wheel (sentence-transformers
# dependency) instead of the much larger default CUDA build. The
# constraints file holds every version exactly (Phase 7a.6), so a rebuild
# installs what was tested, not whatever was released since.
RUN pip install -c ./apps/api/constraints.txt "./apps/api[observability]" --extra-index-url https://download.pytorch.org/whl/cpu

# ── runtime ──────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    AGENT_STATE_DIR=/var/lib/assistant-studio

# No Node.js: claude-agent-sdk ships the Claude Code CLI inside the wheel, as
# a self-contained native binary (claude_agent_sdk/_bundled/claude), and it is
# pinned by the SDK's own version in pyproject.toml. This image used to apt-get
# nodejs, npm and curl for a CLI it never used. Verified by running the bundled
# binary with node off the PATH.
RUN groupadd --system app && useradd --system --gid app --home /app app
# The CLI's working directories and session transcripts (Phase 7a.5,
# AGENT_STATE_DIR). Compose mounts a named volume here; a new volume copies
# this directory's owner, so the app user can write to it.
RUN mkdir -p /var/lib/assistant-studio && chown app:app /var/lib/assistant-studio
COPY --from=builder /opt/venv /opt/venv
COPY --chown=app:app apps/api /app/apps/api
COPY --chown=app:app docker/entrypoint-api.sh /usr/local/bin/entrypoint-api.sh
# A checkout on Windows can hand this file over with CRLF line endings, and
# `sh` then looks for a program called `sh` plus a carriage return. Strip
# them, whatever arrived.
RUN sed -i 's/\r$//' /usr/local/bin/entrypoint-api.sh \
    && chmod +x /usr/local/bin/entrypoint-api.sh

USER app
WORKDIR /app/apps/api
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --retries=10 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/healthz').status==200 else 1)"

ENTRYPOINT ["entrypoint-api.sh"]
# On a stop, uvicorn waits for open connections, and a watched answer is a
# stream that stays open: without a limit it waited until Docker killed it,
# before the app could record its running turns as stopped. After 10 s it
# closes them and shuts down; compose allows 30 s in all (stop_grace_period).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-graceful-shutdown", "10"]
