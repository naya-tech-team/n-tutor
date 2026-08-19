"""Turn the Python dataset into the three S3 objects the deployed system reads.

`hr_data.py` is the source of truth on a laptop. In AWS the source of truth is a
bucket, and this script is the one-way door between them:

    employees/employees.json        12 records
    requisitions/requisitions.json  6 open reqs
    skills/skills.json              24 canonical skills + the alias table

`skills.json` is the one that is easy to forget and the one that breaks the most.
It is what makes `find_by_skill("pyspark")` return people whose records say
"Apache Spark" — the alias table is data, not code, and it has to travel.

    uv run scripts/seed_s3.py                 # write .run/seed/*.json and stop
    uv run scripts/seed_s3.py --upload        # also PUT them to $S3_BUCKET
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from _shared import hr_data, settings  # noqa: E402
from _shared import store  # noqa: E402

SEED = {
    store.EMPLOYEES_KEY: hr_data.EMPLOYEES,
    store.REQUISITIONS_KEY: hr_data.JOBS,
    store.SKILLS_KEY: hr_data.SKILLS,
}


def write_local(out_dir: Path) -> list[Path]:
    written = []
    for key, records in SEED.items():
        path = out_dir / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(records, indent=2) + "\n")
        written.append(path)
    return written


def upload() -> None:
    if not settings.s3_bucket:
        raise SystemExit(
            "S3_BUCKET is empty. Apply terraform/01_s3_data first, then put its "
            "bucket output in agent-core/.env."
        )
    for key, records in SEED.items():
        store.put_object(key, (json.dumps(records, indent=2) + "\n").encode())
        print(f"  put s3://{settings.s3_bucket}/{key}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upload", action="store_true", help="PUT the objects to S3")
    args = parser.parse_args()

    out_dir = settings.seed_dir
    for path in write_local(out_dir):
        print(f"  wrote {path.relative_to(Path.cwd())}" if path.is_relative_to(Path.cwd()) else f"  wrote {path}")

    counts = {k.split("/")[0]: len(v) for k, v in SEED.items()}
    print(f"\n  {counts['employees']} employees · {counts['requisitions']} requisitions · "
          f"{counts['skills']} skills")

    if args.upload:
        print()
        upload()
    else:
        print("\n  --upload to push these to S3.")


if __name__ == "__main__":
    main()
