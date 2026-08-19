"""A per-invocation tool-call budget, as a hook.

Why this exists: the Strands agent loop *recurses* once per cycle, so an agent
that keeps calling tools does not spin harmlessly — it walks off Python's stack
and raises `EventLoopException: maximum recursion depth exceeded`. On a direct
call you stop that with `agent(prompt, limits={"turns": n})`.

Inside an `A2AServer` you have no call site: the server invokes the agent for
you, so there is nowhere to pass `limits`. A hook is the way in — it attaches to
the agent object itself and travels with it.

**A remote agent needs this more than a local one, not less.** When a service
loops, the caller just sees a request that never returns; the evidence is all in
the other team's terminal.
"""

from __future__ import annotations

from strands.hooks import BeforeInvocationEvent, BeforeToolCallEvent, HookProvider, HookRegistry


class ToolBudget(HookProvider):
    """Allow at most `max_calls` tool calls per invocation, then answer from what you have.

    Args:
        max_calls: How many tool calls one question may cost.
    """

    def __init__(self, max_calls: int = 4) -> None:
        self.max_calls = max_calls
        self._used = 0

    def register_hooks(self, registry: HookRegistry, **_) -> None:
        registry.add_callback(BeforeInvocationEvent, self._reset)
        registry.add_callback(BeforeToolCallEvent, self._enforce)

    def _reset(self, event: BeforeInvocationEvent) -> None:
        self._used = 0

    def _enforce(self, event: BeforeToolCallEvent) -> None:
        # Reserve BEFORE the tool runs. Tools in one turn execute concurrently, so
        # a counter incremented afterwards reads zero at every check and enforces
        # nothing — the same bug the plugins lesson hits with a budget guard.
        self._used += 1
        if self._used > self.max_calls:
            # cancel_tool hands this string back to the model as the tool result,
            # so it is a prompt: tell it what to do now, not just that it failed.
            event.cancel_tool = (
                f"Tool budget of {self.max_calls} calls for this question is used up. "
                "Do not call any more tools. Answer now, using only what the earlier "
                "tool results already told you."
            )
