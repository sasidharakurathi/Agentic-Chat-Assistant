"""A canned model for the eval regression gate (task 6.2).

The gate has to be free and the same every time, so there is no model. But
the point of it is to notice when the *platform* changes what an assistant
does: a tool that stops being offered, a guard that stops refusing, a
citation that stops resolving. So everything except the model is real.

`CannedModel` plays the CLI's side of the SDK protocol (`ScriptedCLI`) and,
for each question, does what its **play** says: call these tools with
these inputs, then answer with this text. Each call goes the way the real
CLI sends it: the PreToolUse hooks, then the permission callback, then the
tool on the in-process MCP server. What comes back is whatever the
platform really did, and the answer is built from it:

- `{output}` is the last tool's result;
- `{cite:Title}` is the citation marker `kb_search` printed for the passage
  with that title, or nothing if retrieval didn't return it.

So an answer only says "30 days [1]" if the platform found the passage and
gave it a marker, and a check on that answer fails when it stops doing so.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from tests.scripted_cli import SESSION, Script, ScriptedCLI, assistant, delta, result, tool_result

#: What a canned turn reports it used. Fixed, so a run's cost is a constant.
TOKENS_IN, TOKENS_OUT, COST_USD = 400, 60, 0.002


@dataclass
class Play:
    """What the model does for one question."""

    #: `[{"tool": "mcp__caps__kb_search", "input": {...}}, ...]`, in order.
    steps: list[dict[str, Any]] = field(default_factory=list)
    answer: str = ""


@dataclass
class Call:
    """One tool call as the platform handled it."""

    tool: str
    input: dict[str, Any]
    output: str
    #: "ran", "gate" (refused by the PreToolUse gate) or "permission"
    #: (refused by the permission callback).
    outcome: str


class CannedModel:
    def __init__(self, plays: dict[str, Play], variables: dict[str, str] | None = None) -> None:
        self._plays = plays
        #: `$name` in a step's input is replaced (a connection id, say, that
        #: only exists once the fixture assistant has been built).
        self._vars = variables or {}
        #: Every call made, per question, for the gate's own assertions.
        self.calls: dict[str, list[Call]] = {}

    def _play_for(self, prompt: str) -> tuple[str, Play]:
        """The play whose question the prompt carries. The prompt is the
        guarded one (rules and history may wrap the user's text), so match
        by containment, longest question first."""
        for question in sorted(self._plays, key=len, reverse=True):
            if question in prompt:
                return question, self._plays[question]
        raise AssertionError(f"no play for prompt: {prompt[:200]!r}")

    def _fill(self, value: Any) -> Any:
        if isinstance(value, str):
            for name, real in self._vars.items():
                value = value.replace(f"${name}", real)
            return value
        if isinstance(value, dict):
            return {k: self._fill(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._fill(v) for v in value]
        return value

    async def _call(self, cli: ScriptedCLI, call_id: str, tool: str, args: dict[str, Any]) -> Call:
        """One tool call, the way the CLI makes it."""
        for callback_id in cli.hook_ids("PreToolUse"):
            verdict = await cli.request(
                "hook_callback",
                callback_id=callback_id,
                tool_use_id=call_id,
                input={"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": args},
            )
            out = verdict.get("hookSpecificOutput") or {}
            if out.get("permissionDecision") == "deny":
                return Call(tool, args, str(out.get("permissionDecisionReason", "")), "gate")
            args = out.get("updatedInput") or args
        permission = await cli.request(
            "can_use_tool",
            tool_name=tool,
            input=args,
            tool_use_id=call_id,
            permission_suggestions=[],
        )
        if permission["behavior"] != "allow":
            return Call(tool, args, str(permission.get("message", "")), "permission")
        args = permission.get("updatedInput") or args
        prefix, _, rest = tool.partition("__")
        server, _, name = rest.partition("__")
        if prefix != "mcp" or not name:
            return Call(tool, args, "(a built-in tool: not run by the canned model)", "ran")
        reply = await cli.mcp(server, "tools/call", {"name": name, "arguments": args})
        text = "".join(part.get("text", "") for part in reply.get("content") or [])
        return Call(tool, args, text, "ran")

    @staticmethod
    def _answer(template: str, calls: list[Call]) -> str:
        def cite(match: re.Match[str]) -> str:
            title = re.escape(match.group(1))
            for call in calls:
                found = re.search(rf"^\[(\d+)\] {title}\b", call.output, re.M)
                if found:
                    return f"[{found.group(1)}]"
            return ""

        text = re.sub(r"\{cite:([^}]+)\}", cite, template)
        return text.replace("{output}", calls[-1].output if calls else "")

    def script(self) -> Script:
        async def play(cli: ScriptedCLI) -> None:
            for server in cli.options.mcp_servers or {}:
                await cli.mcp_connect(str(server))
            cli.send({"type": "system", "subtype": "init", "session_id": SESSION, "tools": []})
            question, the_play = self._play_for(json.dumps(cli.prompt, ensure_ascii=False))
            calls: list[Call] = []
            for n, step in enumerate(the_play.steps, start=1):
                call_id = f"toolu_{n}"
                tool, args = str(step["tool"]), self._fill(step.get("input") or {})
                block = {"type": "tool_use", "id": call_id, "name": tool, "input": args}
                cli.send(assistant(f"msg_{n}", [block], TOKENS_IN, 10))
                done = await self._call(cli, call_id, tool, args)
                calls.append(done)
                cli.send(tool_result(call_id, done.output, is_error=done.outcome != "ran"))
            self.calls[question] = calls
            text = self._answer(the_play.answer, calls)
            for m in delta("msg_final", text):
                cli.send(m)
            cli.send(
                assistant("msg_final", [{"type": "text", "text": text}], TOKENS_IN, TOKENS_OUT)
            )
            cli.send(result(TOKENS_IN, TOKENS_OUT, COST_USD))
            cli.end()

        return play


__all__ = ["COST_USD", "Call", "CannedModel", "Play"]
