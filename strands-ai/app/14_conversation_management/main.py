"""14 · Conversation management — what survives when the context window fills.

A long screening conversation: constraints stated at the start, candidates
discussed for twenty turns, then "so who do I interview?". Whether that question
can still be answered depends entirely on which manager you picked.

Run:  uv run app/14_conversation_management/main.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from strands import Agent
from strands.agent.conversation_manager import (
    NullConversationManager,
    SlidingWindowConversationManager,
    SummarizingConversationManager,
)

from _shared import make_model

# The first two turns carry the constraints. The last turn needs them back.
# Everything between is the noise a real screening session generates.
TURNS = [
    "I'm Naveen, hiring manager for requisition J2001, Senior Data Engineer in Bengaluru.",
    "Hard rule for this req: Spark level 4 minimum, and the person must be on the bench today.",
    "Priya Raman scored 100% — Spark 5, Python 4, SQL 5, Airflow 4.",
    "Rahul Menon scored 61% — his Spark is only level 3 and he has no Airflow.",
    "Vikram Iyer is Chennai-based with Kafka 4 but no SQL on record.",
    "Remind me: which requisition am I filling, and what was my hard rule?",
]


def run(label: str, agent: Agent) -> None:
    print(f"=== {label} ===")
    for turn in TURNS:
        result = agent(turn)
    print(f"  messages kept: {len(agent.messages)}")
    print(f"  final answer : {str(result).strip()[:160]}")
    print(f"  context size : {result.context_size} tokens\n")


SYSTEM = "You are a resourcing assistant. Answer in one sentence."


def main() -> None:
    # 1) Sliding window (the default). Oldest messages fall off the end — which
    #    here means the hard rule is the first thing forgotten.
    #    window_size=4 is aggressively small so the memory loss is visible.
    run(
        "1. SlidingWindow(window_size=4) — cheap, forgetful",
        Agent(
            model=make_model(),
            system_prompt=SYSTEM,
            conversation_manager=SlidingWindowConversationManager(window_size=4),
            callback_handler=None,
        ),
    )

    # 2) Sliding window that pins the opening messages — the requisition and the
    #    hard rule survive, the candidate chatter is what gets dropped. For this
    #    use case that is exactly the right trade.
    run(
        "2. SlidingWindow(window_size=4, pin_first=2) — keeps the constraints",
        Agent(
            model=make_model(),
            system_prompt=SYSTEM,
            conversation_manager=SlidingWindowConversationManager(window_size=4, pin_first=2),
            callback_handler=None,
        ),
    )

    # 3) Summarizing. Old turns are compressed, not dropped — the scores stay
    #    roughly recoverable, at the cost of an extra model call.
    run(
        "3. Summarizing(summary_ratio=0.5, preserve_recent_messages=2) — remembers, costs tokens",
        Agent(
            model=make_model(),
            system_prompt=SYSTEM,
            conversation_manager=SummarizingConversationManager(
                summary_ratio=0.5,
                preserve_recent_messages=2,
                proactive_compression={"compression_threshold": 0.6},
            ),
            callback_handler=None,
        ),
    )

    # 4) Null. Nothing is ever removed — you accept the overflow error. Correct
    #    when the transcript is the audit record and must not be paraphrased.
    run(
        "4. Null — full fidelity, will eventually overflow",
        Agent(
            model=make_model(),
            system_prompt=SYSTEM,
            conversation_manager=NullConversationManager(),
            callback_handler=None,
        ),
    )

    # 5) The batteries-included preset: summarizing + offloading big tool results
    #    (a screening run that returns 200 candidate profiles is exactly that).
    run(
        "5. context_manager='auto' — the sensible default for long-running agents",
        Agent(
            model=make_model(),
            system_prompt=SYSTEM,
            context_manager="auto",
            callback_handler=None,
        ),
    )


if __name__ == "__main__":
    main()
