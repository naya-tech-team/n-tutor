# Python Quick Start — runnable code

A tiny **Skills Matcher** that puts the Python: Quick Start concepts into one small program: classes & objects, encapsulation, a **generator**, two **decorators**, and **error handling** with a custom exception.

The domain is the one the whole course uses: **employees have skills rated 1–5, requisitions require skills, and something has to decide who fits.** Learn it here and every later track (FastAPI, FastMCP, Strands) is about the *technology*, not a new problem.

## Table of contents

- [What's inside](#whats-inside)
- [Run it](#run-it)
- [Expected output](#expected-output)
- [Run the tests](#run-the-tests)
- [Troubleshooting](#troubleshooting)
- [Next Steps](#next-steps)

## What's inside

| File | Purpose |
|------|---------|
| `skills_matcher.py` | The app — `Employee`, `Requisition`, `Match`, `Bench`, `@timed`, `@log_calls`, a `candidates_for()` generator. |
| `test_skills_matcher.py` | Pytest tests for every behaviour. |

The one rule worth knowing before you read it: a **mandatory** skill below its
minimum level is a *blocker*. The score still gets reported, but the verdict does
not — "82% but cannot do the mandatory thing" is not a shortlist.

## Run it

No third-party packages needed. Python 3.10+ (3.13 recommended).

```bash
cd python
python3 skills_matcher.py
```

## Expected output

```text
Filling J2001 Senior Data Engineer (Bengaluru)

Adding people to the bench:
  · add('E1002', 'Priya Raman', 'Bengaluru', {'Python': 4, 'Apache Spark': 5, 'SQL': 5, 'Apache Airflow': 4}) -> Employee(...)
  · add('E1003', 'Rahul Menon', 'Hyderabad', {'Python': 4, 'Apache Spark': 3, 'SQL': 4}) -> Employee(...)
  · add('E1005', 'Vikram Iyer', 'Chennai', {'Python': 3, 'Apache Spark': 4, 'Apache Kafka': 4}) -> Employee(...)
  · add('E1006', 'Sneha Kapoor', 'Pune', {'Python': 3, 'SQL': 4, 'dbt': 4}) -> Employee(...)

Trying invalid input:
  ✗ rejected: E1002 is already on the bench
  ✗ rejected: SQL level must be 1..5, got 9

Everyone, scored against J2001:
  Priya Raman        100%  strong   blockers: none
  Rahul Menon         69%  blocked  blockers: Apache Spark
  Vikram Iyer         44%  blocked  blockers: Python, SQL
  Sneha Kapoor        44%  blocked  blockers: Python, Apache Spark

Viable candidates (best first, blockers excluded):
  Priya Raman        100%  strong   blockers: none

First pick: Priya Raman at 100%

  ⏱  summary took 0.01 ms
Data & Analytics bench: 1/4 viable for J2001
```

(The timing number will vary, and the `Employee(...)` lines are abbreviated here —
the real output prints the full dataclass repr.)

Two things worth noticing in that output:

- **Rahul scores 69% and is still blocked.** Spark 3 against a bar of 4 earns
  partial credit — he is a coaching problem, not a hiring problem — but the skill
  is mandatory, so the verdict is `blocked` regardless.
- **The generator only yields Priya.** `candidates_for()` skips blocked people
  entirely, and `next(...)` on it scores just far enough to find the first one.

## Run the tests

```bash
pip install pytest
pytest -q
```

Expected: `14 passed`.

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `python: command not found` | Try `python3` instead. |
| Odd characters instead of `⏱`/`✗` | Your terminal isn't UTF-8; the logic still works. |
| `ModuleNotFoundError: skills_matcher` (in tests) | Run `pytest` from inside this folder. |

## Next Steps

- Read the [Python: Quick Start](../docs/quickstart/python.html) page.
- Then the [FastAPI code](../fast-api/README.md) — the same domain behind an HTTP API.
- Then the [MCP server](../mcp-server/README.md) — the same domain exposed to an AI.
