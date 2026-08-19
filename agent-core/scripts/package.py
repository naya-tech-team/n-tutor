"""Build the deployment zips AgentCore Runtime reads from S3.

Direct code deployment means **you** vendor the dependencies — the service does
not pip-install anything at start-up. And Runtime is **arm64 only**, so wheels
built for your Mac's Python will import fine here and fail there.

    uv pip install --python-platform aarch64-manylinux2014 --python-version 3.13 \\
        --target=build/<name> --only-binary=:all: -r requirements.txt

`--only-binary=:all:` is the load-bearing flag: without it, a package with no
aarch64 wheel silently builds from source *for your machine*, and you get a zip
that works on your laptop and crashes in the container.

Layout of each zip — everything at the root, because `/var/task` is first on
`sys.path`:

    <deps>/          from the uv pip install above
    main.py          the entrypoint
    _shared/         the domain, vendored per artifact

Limits are 250 MB zipped and 750 MB unzipped, and `__pycache__` is excluded:
bytecode compiled on darwin/arm64 is not portable to Amazon Linux.

    uv run scripts/package.py                # all five runtimes + the lambda
    uv run scripts/package.py talent_screening
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
BUILD = ROOT / "build"
DIST = ROOT / "dist"

PYTHON_VERSION = "3.13"
PLATFORM = "aarch64-manylinux2014"

# Runtime dependencies per artifact. Deliberately not the whole pyproject: the
# writer needs no boto3, the lambda needs no strands, and 250 MB is not a lot.
COMMON_OTEL = ["aws-opentelemetry-distro>=0.18.0"]
STRANDS = ["strands-agents[a2a]>=1.51.0", "bedrock-agentcore>=1.21.0", "pydantic-settings>=2.15.0", "boto3"]

# Dependency resolution drags in a lot that this system never touches, and in a
# Runtime container that is not merely untidy — **it is the 30-second
# initialization budget.** The service unpacks the artifact and waits for the
# health check, and `opentelemetry-instrument` walks every installed distribution
# on the way up. A 120 MB / 6,400-file artifact does not make it.
#
#   ValidationException: Runtime initialization time exceeded. Please make sure
#   that initialization completes in 30s.
#
# `strands-agents[a2a]` pulls a PostgreSQL driver, an ORM and gRPC — a2a-sdk
# supports a database task store and a gRPC transport, and this system uses
# neither. Between them that is ~45 MB and ~900 files.
#
# Each entry maps an import name to the dist-info prefixes that own it, so the
# metadata goes with the package. Leaving the dist-info behind is worse than
# leaving the package: the instrumentation loader finds the distribution, tries
# to import it, and fails.
#
# **Verified, not assumed.** `sqlalchemy` and `greenlet` ARE imported by
# `strands.multiagent.a2a` — so the three A2A servers keep them, and only the
# runtimes that merely *call* A2A drop them. Check before adding to this list:
#
#   uv run python -c "import sys; before=set(sys.modules); \
#     import a2a.client; print([m for m in set(sys.modules)-before])"
PRUNABLE = {
    "grpc": ("grpcio",),
    "grpc_status": ("grpcio_status",),
    "asyncpg": ("asyncpg",),
    "sqlalchemy": ("sqlalchemy", "SQLAlchemy"),
    "greenlet": ("greenlet",),
}

# Never imported by anything here, on either the client or the server path.
PRUNE_ALWAYS = ["grpc", "grpc_status", "asyncpg"]

# Plus the ORM, for artifacts that call A2A but do not serve it.
PRUNE_A2A_CLIENT_ONLY = [*PRUNE_ALWAYS, "sqlalchemy", "greenlet"]

ARTIFACTS = {
    # Calls A2A, never serves it — so it drops the ORM as well.
    "hiring_supervisor": {
        "entry": APP / "runtimes/hiring_supervisor/main.py",
        "extra": [APP / "clients"],
        "deps": [*STRANDS, "bedrock-agentcore[strands-agents]>=1.21.0", *COMMON_OTEL],
        "prune": PRUNE_A2A_CLIENT_ONLY,
    },
    # The next three serve A2A. `strands.multiagent.a2a` imports sqlalchemy and
    # greenlet at module scope, so those stay.
    "talent_screening": {
        "entry": APP / "runtimes/talent_screening/main.py",
        "extra": [APP / "clients"],
        "deps": [*STRANDS, "mcp>=1.29.0", *COMMON_OTEL],
        "prune": PRUNE_ALWAYS,
    },
    "recruiting_outreach": {
        "entry": APP / "runtimes/recruiting_outreach/main.py",
        "extra": [],
        "deps": [*STRANDS, *COMMON_OTEL],
        "prune": PRUNE_ALWAYS,
    },
    "people_compliance": {
        "entry": APP / "runtimes/people_compliance/main.py",
        "extra": [],
        "deps": [*STRANDS, *COMMON_OTEL],
        "prune": PRUNE_ALWAYS,
    },
    "hr_skills_mcp": {
        "entry": APP / "runtimes/hr_skills_mcp/main.py",
        "extra": [],
        "deps": ["fastmcp>=3.4.6", "pydantic-settings>=2.15.0", "boto3", *COMMON_OTEL],
        "prune": PRUNE_A2A_CLIENT_ONLY,
    },
    # The Lambda is not an AgentCore runtime, but it is the same problem: an
    # arm64 zip with its dependencies vendored.
    "hr_data_fn": {
        "entry": APP / "lambda_fn/handler.py",
        "extra": [],
        "deps": ["pydantic-settings>=2.15.0"],  # boto3 is in the Lambda runtime already
    },
}

EXCLUDE = {"__pycache__", ".DS_Store", ".pytest_cache"}


# The chat proxy is the one Node artifact, and it is Node for a reason that is
# worth knowing before you try to rewrite it in Python: Lambda response streaming
# exists on Node.js managed runtimes and custom runtimes only. There is no
# `streamifyResponse` for the Python managed runtime, and without streaming the
# browser waits out three delegations with nothing on screen.
#
# No cross-compilation problem here, unlike the Python zips — the SDK is pure
# JavaScript, so there is no arm64 wheel to get wrong.
NODE_ARTIFACTS = {
    "chat_proxy": {"src": ROOT / "ui/proxy", "runtime": "nodejs"},
}


def install_deps(name: str, deps: list[str], target: Path) -> None:
    print(f"    deps -> {target.relative_to(ROOT)}")
    subprocess.run(
        [
            "uv", "pip", "install",
            "--python-platform", PLATFORM,
            "--python-version", PYTHON_VERSION,
            "--target", str(target),
            "--only-binary=:all:",
            *deps,
        ],
        check=True,
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
    )


def prune(stage: Path, names: list[str]) -> None:
    """Delete vendored packages this artifact provably does not import.

    Prints what went and what it saved, because a silent size optimisation is one
    nobody re-checks when an import starts failing.
    """
    if not names:
        return

    freed = 0
    removed = []
    for name in names:
        targets = [stage / name]
        for prefix in PRUNABLE.get(name, ()):
            targets += [
                p for p in stage.glob(f"{prefix}*")
                if p.is_dir() and p.suffix in {".dist-info", ".libs"}
            ]
        for path in targets:
            if not path.exists():
                continue
            freed += sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
            shutil.rmtree(path, ignore_errors=True)
            removed.append(path.name)

    if removed:
        print(f"    pruned {len(removed)} dirs, {freed / 1024**2:.1f} MB: {', '.join(sorted(removed)[:6])}")


def copy_tree(src: Path, dst: Path) -> None:
    shutil.copytree(
        src, dst, dirs_exist_ok=True, ignore=shutil.ignore_patterns(*EXCLUDE)
    )


def build(name: str, spec: dict) -> Path:
    print(f"  {name}")
    stage = BUILD / name
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True)

    install_deps(name, spec["deps"], stage)
    prune(stage, spec.get("prune", []))

    # main.py at the root — that is what `entry_point = ["main.py"]` refers to.
    shutil.copy2(spec["entry"], stage / "main.py")
    copy_tree(APP / "_shared", stage / "_shared")
    for extra in spec["extra"]:
        copy_tree(extra, stage / extra.name)

    out = DIST / f"{name}.zip"
    zipped, unzipped = zip_dir(stage, out)

    flag = ""
    if zipped > 250 * 1024**2:
        flag = "  ✗ OVER the 250 MB zipped limit"
    elif unzipped > 750 * 1024**2:
        flag = "  ✗ OVER the 750 MB unzipped limit"
    print(f"    {out.relative_to(ROOT)}  {zipped/1024**2:.1f} MB zipped, "
          f"{unzipped/1024**2:.1f} MB unzipped{flag}")
    return out


def zip_dir(stage: Path, out: Path) -> tuple[int, int]:
    """Zip everything under `stage`, flat at the root. Returns (zipped, unzipped)."""
    out.parent.mkdir(exist_ok=True)
    out.unlink(missing_ok=True)

    unzipped = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(stage.rglob("*")):
            if any(part in EXCLUDE for part in path.parts):
                continue
            if path.is_file():
                unzipped += path.stat().st_size
                zf.write(path, path.relative_to(stage))
    return out.stat().st_size, unzipped


def build_node(name: str, spec: dict) -> Path:
    """Same job as build(), different package manager.

    `--omit=dev` is what keeps this a few MB rather than a few hundred: without
    it npm vendors the whole toolchain into a Lambda that needs one SDK client.
    """
    print(f"  {name}  (node)")
    stage = BUILD / name
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True)

    for item in ("index.mjs", "package.json"):
        shutil.copy2(spec["src"] / item, stage / item)

    # `npm ci` when there is a lockfile to honour, `npm install` when there is
    # not. The difference matters for an artifact: `install` resolves the semver
    # range afresh every build, so two zips cut from the same commit can ship
    # different SDK versions. Deliberately not silent about which one ran.
    lock = spec["src"] / "package-lock.json"
    if lock.exists():
        shutil.copy2(lock, stage / lock.name)
        command = ["npm", "ci", "--omit=dev"]
    else:
        command = ["npm", "install", "--omit=dev"]

    print(f"    {' '.join(command)}")
    subprocess.run(
        [*command, "--no-audit", "--no-fund", "--silent"],
        check=True,
        cwd=stage,
        stdout=subprocess.DEVNULL,
    )

    zipped, unzipped = zip_dir(stage, DIST / f"{name}.zip")
    print(f"    dist/{name}.zip  {zipped/1024**2:.1f} MB zipped, {unzipped/1024**2:.1f} MB unzipped")
    return DIST / f"{name}.zip"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("names", nargs="*", help="artifacts to build (default: all)")
    parser.add_argument("--keep-build", action="store_true", help="leave build/ in place")
    args = parser.parse_args()

    known = {**ARTIFACTS, **NODE_ARTIFACTS}
    names = args.names or list(known)
    unknown = [n for n in names if n not in known]
    if unknown:
        raise SystemExit(f"unknown artifact(s) {unknown}. Known: {sorted(known)}")

    print(f"building for {PLATFORM} / python {PYTHON_VERSION}\n")
    for name in names:
        if name in NODE_ARTIFACTS:
            build_node(name, NODE_ARTIFACTS[name])
        else:
            build(name, ARTIFACTS[name])

    if not args.keep_build:
        shutil.rmtree(BUILD, ignore_errors=True)
    print(f"\n  upload with: uv run scripts/seed_s3.py --upload  (data)")
    print(f"  then point terraform/05_runtimes at {DIST.relative_to(ROOT)}/")


if __name__ == "__main__":
    sys.exit(main())
