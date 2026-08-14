"""08 · Agent loop — make the invisible loop visible.

"Who should we interview for J2002?" is a two-hop question: read the
requisition, then screen against it. Watch the cycles that answer it.

Run:  uv run app/08_agent_loop/main.py
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from strands import Agent, tool
from strands.hooks import (
    AfterModelCallEvent,
    AfterToolCallEvent,
    BeforeInvocationEvent,
    BeforeModelCallEvent,
    BeforeToolCallEvent,
    HookProvider,
    HookRegistry,
)

from _shared import get_job, make_model, rank_candidates


@tool
def get_requisition(job_id: str) -> str:
    """Look up an open requisition and the skills it requires.

    Args:
        job_id: e.g. "J2002"
    """
    job = get_job(job_id)
    if job is None:
        return f"no requisition {job_id}"
    reqs = ", ".join(
        f"{r['skill']} L{r['min_level']}{'*' if r['mandatory'] else ''}" for r in job["required_skills"]
    )
    return f"{job['title']} in {job['location']}, {job['min_experience_years']}+ yrs. Requires: {reqs} (* = mandatory)"


@tool
def screen_bench(job_id: str) -> str:
    """Score every available employee against a requisition, best first.

    Args:
        job_id: e.g. "J2002"
    """
    results = rank_candidates(job_id, available_only=True, limit=4)
    if not results:
        return f"no requisition {job_id}"
    return "\n".join(f"{r['name']} ({r['employee_id']}): {r['score']}% {r['verdict']}" for r in results)


class LoopTracer(HookProvider):
    """Prints one line per loop event so the cycle structure is obvious."""

    def __init__(self) -> None:
        self.cycle = 0
        self.t0 = 0.0

    def register_hooks(self, registry: HookRegistry, **_) -> None:
        registry.add_callback(BeforeInvocationEvent, self.on_start)
        registry.add_callback(BeforeModelCallEvent, self.on_model_start)
        registry.add_callback(AfterModelCallEvent, self.on_model_end)
        registry.add_callback(BeforeToolCallEvent, self.on_tool_start)
        registry.add_callback(AfterToolCallEvent, self.on_tool_end)

    def on_start(self, event: BeforeInvocationEvent) -> None:
        self.cycle = 0
        self.t0 = time.perf_counter()
        print("── invocation start ──")

    def on_model_start(self, event: BeforeModelCallEvent) -> None:
        self.cycle += 1
        print(f"  cycle {self.cycle}: → model")

    def on_model_end(self, event: AfterModelCallEvent) -> None:
        stop = event.stop_response.stop_reason if event.stop_response else "?"
        print(f"  cycle {self.cycle}: ← model (stop_reason={stop})")

    def on_tool_start(self, event: BeforeToolCallEvent) -> None:
        print(f"  cycle {self.cycle}:   ⚙ {event.tool_use['name']}({event.tool_use['input']})")

    def on_tool_end(self, event: AfterToolCallEvent) -> None:
        print(f"  cycle {self.cycle}:   ✓ {event.tool_use['name']} -> {event.result['status']}")


def demo_full_loop() -> None:
    """A two-hop question forces at least three cycles: read the req, screen, answer."""
    print("=== 1. Watching the cycles ===")
    agent = Agent(
        model=make_model(),
        system_prompt=(
            "You are a resourcing assistant. Read the requisition first with get_requisition, "
            "then call screen_bench with the same job id, then answer."
        ),
        tools=[get_requisition, screen_bench],
        hooks=[LoopTracer()],
        callback_handler=None,
    )

    result = agent("Who should we interview for J2002?")
    print("\nanswer:", str(result).strip())
    print("stop_reason:", result.stop_reason)

    m = result.metrics
    print(f"cycles={m.cycle_count} tokens={m.accumulated_usage} latency_ms={m.accumulated_metrics['latencyMs']}")
    for name, tm in m.tool_metrics.items():
        print(f"  tool {name}: calls={tm.call_count} errors={tm.error_count} total_time={tm.total_time:.2f}s")
    print()


def demo_stop_reasons() -> None:
    """Every invocation ends for a reason. Branch on it, never on the text."""
    print("=== 2. stop_reason is the control signal ===")
    agent = Agent(model=make_model(), tools=[get_requisition, screen_bench], callback_handler=None)

    normal = agent("Say hello in five words.")
    capped = agent("Read J2001, screen the bench, then summarise.", limits={"turns": 1})

    for label, result in (("normal", normal), ("capped", capped)):
        print(f"  {label:8s} stop_reason={result.stop_reason}")

    print("\n  messages after the capped run:", len(agent.messages), "(still re-invokable)")


def main() -> None:
    demo_full_loop()
    demo_stop_reasons()


if __name__ == "__main__":
    main()
