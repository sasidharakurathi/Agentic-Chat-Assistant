"""Guardrails (task 5.3): checks on what goes into the model and what comes
out of its tools. `injection` spots text trying to act as instructions;
`pii` finds and redacts personal data. Where each is applied is in
`agent/hooks.py` (before a tool runs), `agent/post_tool.py` (after) and
`agent/runtime.py` (the user's message)."""
