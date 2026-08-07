# Python Quick Start — runnable code

A tiny **Task Tracker** that puts the Python: Quick Start concepts into one small program: classes & objects, encapsulation, a **generator**, two **decorators**, and **error handling** with a custom exception.

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
| `task_tracker.py` | The app — `Task`, `TaskList`, `@timed`, `@log_calls`, a `pending()` generator. |
| `test_task_tracker.py` | Pytest tests for every behaviour. |

## Run it

No third-party packages needed. Python 3.10+ (3.13 recommended).

```bash
cd quickstart/code/python
python task_tracker.py
```

## Expected output

```text
Adding tasks:
  · add('Write Terraform config',) -> Task(title='Write Terraform config', priority=1, done=False)
  · add('Build MCP server',) -> Task(title='Build MCP server', priority=2, done=False)
  · add('Wire up Strands agent',) -> Task(title='Wire up Strands agent', priority=2, done=False)
  · add('Write docs',) -> Task(title='Write docs', priority=4, done=False)

Trying an invalid task:
  ✗ rejected: title cannot be empty

Still pending (high priority first):
  [ ] P1 Write Terraform config
  [ ] P2 Wire up Strands agent
  [ ] P4 Write docs

  ⏱  summary took 0.01 ms
Launch AI agent: 1/4 done
```

(The timing number will vary; the invalid-task check fails on the empty title before it ever looks at the priority.)

## Run the tests

```bash
pip install pytest
pytest -q
```

Expected: `7 passed`.

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `python: command not found` | Try `python3` instead. |
| Odd characters instead of `⏱`/`✗` | Your terminal isn't UTF-8; the logic still works. |
| `ModuleNotFoundError: task_tracker` (in tests) | Run `pytest` from inside this folder. |

## Next Steps

- Read the [Python: Quick Start](../../python.html) page.
- Then the [FastAPI code](../fastapi/README.md) to put Python behind a web API.
