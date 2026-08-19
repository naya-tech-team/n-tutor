"""Start all four background processes, so you need one terminal instead of five.

`a2a-strands/` deliberately makes you open three terminals — watching a tool call
land in someone else's window is the lesson. That lesson is already learned by the
time you get here, and five terminals is just friction.

Logs stream to `.run/logs/<name>.log`. Tail the screener while the supervisor
runs and you will see `[screening] rank_for_requisition(...)` appear the instant
the delegation lands, exactly as it does across terminals.

    uv run scripts/run_local.py            # start, wait for ready, then idle
    uv run scripts/run_local.py --pipeline # ... then run J2001 end to end and exit
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from _shared import settings  # noqa: E402

SERVICES = [
    ("hr_skills_mcp", "app/runtimes/hr_skills_mcp/main.py", 8000, "/mcp"),
    ("talent_screening", "app/runtimes/talent_screening/main.py", 9001, "/.well-known/agent-card.json"),
    ("recruiting_outreach", "app/runtimes/recruiting_outreach/main.py", 9002, "/.well-known/agent-card.json"),
    ("people_compliance", "app/runtimes/people_compliance/main.py", 9007, "/.well-known/agent-card.json"),
]


def is_listening(port: int, path: str) -> bool:
    """Is anything answering on this port right now?"""
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=2)
        return True
    except urllib.error.HTTPError:
        # An MCP server 400s a bare GET. That is still proof it is listening,
        # which is all we are asking.
        return True
    except Exception:  # noqa: BLE001 — nothing there
        return False


def check_ports_free() -> list[str]:
    """Refuse to start on top of something already running.

    Uvicorn logs `[Errno 48] address already in use` and exits, but the port keeps
    answering — so the pipeline runs happily against a *stale* agent carrying an
    older prompt, and the only symptom is an answer that makes no sense. Ask first.
    """
    return [f"{name} :{port}" for name, _, port, path in SERVICES if is_listening(port, path)]


def wait_for(port: int, path: str, timeout: float = 90) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_listening(port, path):
            return True
        time.sleep(1)
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pipeline", action="store_true", help="run J2001 end to end, then stop")
    parser.add_argument("--job", default="J2001")
    args = parser.parse_args()

    busy = check_ports_free()
    if busy:
        print("  ports already in use: " + ", ".join(busy))
        print("  Something is already listening there. Starting anyway would leave you")
        print("  talking to that process — possibly an older build — while this one logs")
        print("  'address already in use' and exits. Stop it first:")
        print("    pkill -f 'runtimes/(hr_skills_mcp|talent_screening|recruiting_outreach|people_compliance)'")
        raise SystemExit(1)

    log_dir = settings.run_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    procs = []
    try:
        for name, script, port, path in SERVICES:
            log = (log_dir / f"{name}.log").open("w")
            procs.append(
                (name, subprocess.Popen([sys.executable, script], cwd=ROOT, stdout=log, stderr=log))
            )
            print(f"  starting {name:22} :{port}")

        for name, script, port, path in SERVICES:
            ok = wait_for(port, path)
            print(f"  {'ready  ' if ok else 'FAILED '} {name:22} :{port}")
            if not ok:
                print(f"\n  see {log_dir / f'{name}.log'}")
                return

        if args.pipeline:
            print()
            subprocess.run(
                [sys.executable, "app/runtimes/hiring_supervisor/main.py", args.job], cwd=ROOT
            )
            return

        print(f"\n  logs: {log_dir}")
        print(f"  now run: uv run app/runtimes/hiring_supervisor/main.py {args.job}")
        print("  ctrl-c to stop everything")
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        for name, proc in procs:
            proc.terminate()
        print("\n  stopped")


if __name__ == "__main__":
    main()
