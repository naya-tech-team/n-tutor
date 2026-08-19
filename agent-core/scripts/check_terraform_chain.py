"""Does every step's required input come from an earlier step's output?

`terraform validate` does not answer this. It checks one directory's syntax and
schema in isolation, so a stack where 06 needs `gateway_arn` and 03 never exports
it validates perfectly and then cannot apply.

That is exactly what happened here: six green `validate` runs, and five reasons
the stack could not deploy. This script is that audit, kept.

It also checks the other half — that the environment variables the runtimes set
match the field names `_shared/config.py` actually reads. A typo there does not
fail: `agent_url()` falls back to 127.0.0.1, which inside a container reaches
nothing at all.

    uv run scripts/check_terraform_chain.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TF = ROOT / "terraform"

VARIABLE = re.compile(r'^variable\s+"([a-z0-9_]+)"\s*\{(.*?)^\}', re.M | re.S)
OUTPUT = re.compile(r'^output\s+"([a-z0-9_]+)"', re.M)
RESOURCE_OR_DATA = re.compile(r'^(?:resource|data)\s+"([a-z0-9_]+)"\s+"([a-z0-9_]+)"', re.M)
ENV_KEY = re.compile(r"^\s{4,}([A-Z][A-Z0-9_]*)\s*=", re.M)

# Set by AWS or by the platform, not by us. `_shared/config.py` never reads
# these, so the "does Settings have this field?" check below must skip them.
PLATFORM_ENV = {
    "AGENTCORE",
    "AGENT_OBSERVABILITY_ENABLED",
    "UNIFIED_TRACES_DESTINATION_ENABLED",
    # Read by opentelemetry-instrument, before any of our code runs.
    "OTEL_PYTHON_DISABLED_INSTRUMENTATIONS",
}

# Variables you supply, rather than ones a previous step produces. Both of these
# are choices no earlier `terraform output` can make for you: a password, and a
# model id that depends on what your account has enabled. They still have to
# appear in example.tfvars, which the second check enforces.
HUMAN_INPUT = {"password", "bedrock_model_id"}


ROOT_MODULE = "00_all_at_once"


def steps() -> list[Path]:
    """The numbered layers, in order. Not the root module that composes them."""
    return sorted(
        d for d in TF.iterdir()
        if d.is_dir() and d.name[0].isdigit() and d.name != ROOT_MODULE
    )


def all_vars_named(text: str) -> list[tuple[str, str]]:
    """Every declared variable, as (name, body)."""
    return VARIABLE.findall(text)


def required_vars(text: str) -> list[str]:
    return [name for name, body in VARIABLE.findall(text) if "default" not in body]


def check_chain() -> list[str]:
    """Every required variable must be an output of some earlier step."""
    problems: list[str] = []
    seen: dict[str, str] = {}  # output name -> which step produced it

    for step in steps():
        text = (step / "main.tf").read_text()

        for name in required_vars(text):
            if name in HUMAN_INPUT:
                continue
            if name not in seen:
                problems.append(
                    f"{step.name}: requires var '{name}', which no earlier step outputs"
                )

        for name in OUTPUT.findall(text):
            seen.setdefault(name, step.name)

    return problems


def all_vars(text: str) -> list[tuple[str, bool]]:
    """Every declared variable, with whether it has a default."""
    return [(name, "default" in body) for name, body in VARIABLE.findall(text)]


def check_tfvars() -> list[str]:
    """`example.tfvars` must show the module's COMPLETE variable surface.

    Two different bars, because the two kinds of variable fail differently:

      required   must be actually set — an uncommented `name = ...` line. Miss
                 one and `apply` stops and asks you interactively.
      optional   must at least be *mentioned*, commented out with its default.
                 Miss one and nothing breaks; you simply never learn the knob
                 exists, which is how `retention_days` stays 30 forever.

    Checked against `example.tfvars` specifically, not any `*.tfvars`. That file
    is the committed reference a fresh clone reads; your own `dev.tfvars` is
    gitignored and proves nothing to anyone else.
    """
    problems: list[str] = []
    for step in [TF / ROOT_MODULE, *steps()]:
        example = step / "example.tfvars"
        if not example.exists():
            problems.append(f"{step.name}: no example.tfvars")
            continue

        body = example.read_text()
        for name, has_default in all_vars((step / "main.tf").read_text()):
            # Uncommented assignment.
            is_set = re.search(rf"^\s*{name}\s*=", body, re.M)
            # Mentioned at all, including `# name = "default"`.
            is_shown = re.search(rf"^\s*#?\s*{name}\s*=", body, re.M)

            if not has_default and not is_set:
                problems.append(f"{step.name}: example.tfvars does not set required '{name}'")
            elif has_default and not is_shown:
                problems.append(
                    f"{step.name}: example.tfvars never mentions '{name}' "
                    "— show it commented out with its default"
                )
    return problems


def check_root_passes_everything() -> list[str]:
    """The composing root must pass every required variable to every child.

    Terraform catches this at plan time, but only once you have credentials and
    have waited for an init. Catching it here costs nothing.
    """
    root = TF / ROOT_MODULE / "main.tf"
    if not root.exists():
        return [f"{ROOT_MODULE}/main.tf is missing"]
    text = root.read_text()

    # module "name" { source = "../01_s3_data" ... } -> the block body per source
    blocks = re.findall(r'module\s+"[a-z0-9_]+"\s*\{(.*?)\n\}', text, re.S)
    by_source = {}
    for body in blocks:
        found = re.search(r'source\s*=\s*"\.\./([0-9a-z_]+)"', body)
        if found:
            by_source[found.group(1)] = body

    problems = []
    for step in steps():
        body = by_source.get(step.name)
        if body is None:
            problems.append(f"{ROOT_MODULE}: never calls {step.name}")
            continue
        for name in required_vars((step / "main.tf").read_text()):
            if not re.search(rf"^\s*{name}\s*=", body, re.M):
                problems.append(f"{ROOT_MODULE}: calls {step.name} without '{name}'")
    return problems


def check_artifact_keys_are_content_addressed() -> list[str]:
    """A rebuilt zip must change something the runtime can see.

    An AgentCore runtime points at its code as bucket + prefix. If the prefix is a
    fixed key, rebuilding the artifact replaces the bytes in S3 and changes nothing
    on the runtime — no diff, no update, no new version — and the container goes on
    running whatever it started with.

    Nothing reports that. `terraform apply` shows the S3 object updating, the new
    artifact really is in the bucket, and every code fix is live in S3 and dead in
    production. The only tell is an `agent_runtime_version` that never moves.

    So: the artifact's key must depend on the artifact's content.
    """
    runtimes = next((d for d in steps() if d.name.endswith("_runtimes")), None)
    if runtimes is None:
        return ["no *_runtimes step found"]

    text = (runtimes / "main.tf").read_text()
    block = re.search(
        r'resource\s+"aws_s3_object"\s+"artifact"\s*\{(.*?)^\}', text, re.M | re.S
    )
    if block is None:
        return [f"{runtimes.name}: no aws_s3_object.artifact to check"]

    key = re.search(r'^\s*key\s*=\s*(.+?)\s*$', block.group(1), re.M)
    if key is None:
        return [f"{runtimes.name}: aws_s3_object.artifact has no key"]

    # Either filemd5() inline, or a local holding one.
    if "filemd5" in key.group(1) or "hash" in key.group(1):
        return []
    return [
        f"{runtimes.name}: artifact key {key.group(1).strip()} does not depend on the "
        "zip's content — a rebuild will not produce a new runtime version, and the "
        "container will keep running old code"
    ]


def env_blocks(text: str) -> str:
    """Just the parts of the file that actually define container environments.

    Scanning the whole file was near enough until an output grew a shell snippet:
    `ENCODED=$(...)` in a heredoc looks exactly like an environment variable to a
    line-based regex, and the check then reports a Settings field that was never
    supposed to exist. Bracket-matched from each definition instead.
    """
    chunks = []
    for opener in ("base_env = {", "environment_variables = merge("):
        start = 0
        while (at := text.find(opener, start)) != -1:
            depth, i = 0, at + len(opener) - 1
            while i < len(text):
                if text[i] in "{(":
                    depth += 1
                elif text[i] in "})":
                    depth -= 1
                    if depth == 0:
                        break
                i += 1
            chunks.append(text[at:i])
            start = i or at + 1
    return "\n".join(chunks)


def check_env_names() -> list[str]:
    """Env vars the runtimes set must be fields Settings actually reads."""
    runtimes = next((d for d in steps() if d.name.endswith("_runtimes")), None)
    if runtimes is None:
        return ["no *_runtimes step found"]

    config = (ROOT / "app/_shared/config.py").read_text()
    fields = set(re.findall(r"^\s{4}([a-z][a-z0-9_]*)\s*:", config, re.M))

    scanned = env_blocks((runtimes / "main.tf").read_text())
    if not scanned:
        return [f"{runtimes.name}: found no environment_variables blocks to check"]

    problems = []
    for key in sorted(set(ENV_KEY.findall(scanned))):
        if key in PLATFORM_ENV:
            continue
        if key.lower() not in fields:
            problems.append(
                f"{runtimes.name}: sets {key}, but Settings has no '{key.lower()}' field "
                "— this fails silently, not loudly"
            )
    return problems


def check_invoke_grants_are_scoped() -> list[str]:
    """No runtime may hold `bedrock-agentcore:Invoke*` on `"*"`.

    This became a real check the day the inner runtimes moved from CUSTOM_JWT to
    SigV4. Before that a token was the gate and the IAM grant was a second lock;
    after it, **IAM is the only check between services** — so `resources = ["*"]`
    means the outreach agent may invoke the supervisor and the screener may invoke
    anything in the account.

    Nothing fails when it is wrong. Every delegation still works, which is exactly
    why the wildcard survived a rewrite of the auth model with a comment beside it
    reading "only the supervisor may delegate".

    Invoke actions only. `CreateEvent` and the memory reads are still `"*"`, which
    is recorded in terraform/README.md rather than silently permitted here.
    """
    text = (TF / "05_runtimes" / "main.tf").read_text()
    problems: list[str] = []

    for block in re.findall(r"content\s*\{(.*?)\n\s{4}\}", text, re.S):
        actions = re.search(r"actions\s*=\s*\[(.*?)\]", block, re.S)
        resources = re.search(r"resources\s*=\s*\[(.*?)\]", block, re.S)
        if not actions or not resources:
            continue
        invoke = [a for a in re.findall(r'"([^"]+)"', actions.group(1)) if ":Invoke" in a]
        if invoke and '"*"' in resources.group(1).strip():
            problems.append(
                f"05_runtimes: {', '.join(invoke)} granted on \"*\" — scope it to "
                "local.callees / var.gateway_arn"
            )
    return problems


def check_discovery_is_granted() -> list[str]:
    """`InvokeAgentRuntime` without `GetAgentCard` is a delegation that never starts.

    An A2A conversation is two calls — fetch the card, then send the message — and
    they are two IAM actions. Granting only the second is the failure this check
    exists for, because it fails *before* the part you granted:

        GET .../invocations/.well-known/agent-card.json  403 Forbidden

    which reads like the remote agent is down. `simulate-principal-policy` shows
    `allowed` for InvokeAgentRuntime beside `implicitDeny` for GetAgentCard.

    Paired, not merely present: a grant on some other statement would satisfy a
    weaker check while leaving this edge broken.
    """
    text = (TF / "05_runtimes" / "main.tf").read_text()
    problems: list[str] = []

    for block in re.findall(r"content\s*\{(.*?)\n\s{4}\}", text, re.S):
        actions = re.search(r"actions\s*=\s*\[(.*?)\]", block, re.S)
        if not actions:
            continue
        granted = set(re.findall(r'"([^"]+)"', actions.group(1)))
        if "bedrock-agentcore:InvokeAgentRuntime" in granted and (
            "bedrock-agentcore:GetAgentCard" not in granted
        ):
            problems.append(
                "05_runtimes: InvokeAgentRuntime granted without GetAgentCard — "
                "discovery is a separate action and fails first"
            )
    return problems


MODULE_HEADING = re.compile(r"^## (0\d_[a-z0-9_]+)", re.M)


def readme_sections() -> dict[str, str]:
    """terraform/README.md, split into one string per module.

    Note `[a-z0-9_]` and not `[a-z_]`: `01_s3_data` has a digit in the middle, and
    a pattern that misses it silently drops a whole module from this audit rather
    than failing. That mistake has been made twice in this file's history.
    """
    text = (TF / "README.md").read_text()
    sections: dict[str, list[str]] = {}
    current = None
    for line in text.splitlines():
        found = MODULE_HEADING.match(line)
        if found:
            current = found.group(1)
            sections[current] = []
        elif current is not None:
            sections[current].append(line)
    return {name: "\n".join(body) for name, body in sections.items()}


def check_readme_is_current() -> list[str]:
    """Every variable, output, resource and data source must appear in the docs.

    The reference is hand-written, which is the only way it says anything worth
    reading — and it is also why it rots. Nothing about adding a variable makes
    the prose wrong in a way anyone notices; you find out when a fresh clone sets
    a knob that does not exist, or misses the one it needed.

    Deliberately weak: it asks whether the name appears *anywhere* in that
    module's section, not that a particular table lists it. A stricter check would
    dictate the shape of the prose, and prose that has to satisfy a parser stops
    explaining anything.

    The reverse direction — a `aws_*.name` documented that no module defines —
    is checked too, against every module, so cross-references stay legal.
    """
    sections = readme_sections()
    declared: set[str] = set()
    for step in [TF / ROOT_MODULE, *steps()]:
        body = (step / "main.tf").read_text()
        declared |= {f"{a}.{b}" for a, b in RESOURCE_OR_DATA.findall(body)}

    problems: list[str] = []
    for step in [TF / ROOT_MODULE, *steps()]:
        text = (step / "main.tf").read_text()
        section = sections.get(step.name)
        if section is None:
            problems.append(f"{step.name}: no `## {step.name}` section in terraform/README.md")
            continue

        undocumented = [f"var {n}" for n, _ in all_vars_named(text) if f"`{n}`" not in section]
        undocumented += [f"output {n}" for n in OUTPUT.findall(text) if f"`{n}`" not in section]
        undocumented += [
            f"{a}.{b}" for a, b in RESOURCE_OR_DATA.findall(text) if f"{a}.{b}" not in section
        ]
        if undocumented:
            problems.append(f"{step.name}: undocumented — {', '.join(undocumented)}")

        stale = {t for t in re.findall(r"(aws_[a-z0-9_]+\.[a-z0-9_]+)", section) if t not in declared}
        if stale:
            problems.append(f"{step.name}: documents things no module defines — {', '.join(sorted(stale))}")

    return problems


def main() -> int:
    checks = [
        ("step inputs come from earlier outputs", check_chain),
        ("the composing root passes every required var", check_root_passes_everything),
        ("example.tfvars shows every variable", check_tfvars),
        ("a rebuilt artifact reaches the container", check_artifact_keys_are_content_addressed),
        ("service-to-service invoke is scoped", check_invoke_grants_are_scoped),
        ("A2A discovery is granted with A2A invoke", check_discovery_is_granted),
        ("terraform/README.md documents every knob", check_readme_is_current),
        ("runtime env vars match Settings fields", check_env_names),
    ]

    failed = 0
    for label, fn in checks:
        problems = fn()
        if problems:
            failed += 1
            print(f"  ✗ {label}")
            for p in problems:
                print(f"      {p}")
        else:
            print(f"  ✓ {label}")

    print()
    if failed:
        print(f"  {failed} check(s) failed")
        return 1
    print(f"  {len(steps())} steps chain cleanly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
