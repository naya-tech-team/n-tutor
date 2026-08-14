"""10 · Storage — one bytes-in/bytes-out interface behind every persistence feature.

What a resourcing agent persists: shortlists per requisition, per business unit,
with candidate PII that must never hit the disk in the clear.

Run:  uv run app/10_storage/main.py
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from strands.storage import InMemoryStorage, LocalFileStorage, Storage

from _shared import rank_candidates, settings


async def demo_four_operations(storage: Storage, label: str) -> None:
    """Every backend supports exactly these four calls. Nothing else."""
    print(f"=== {label} ===")

    # Real payloads: the shortlist the matching engine produced for two reqs.
    for job_id in ("J2001", "J2003"):
        blob = json.dumps(rank_candidates(job_id, limit=3)).encode()
        await storage.write(f"shortlists/{job_id}.json", blob)
    await storage.write("audit/2026-08-10.log", b"R-8812 opened J2001")

    raw = await storage.read("shortlists/J2001.json")
    top = json.loads(raw)[0]
    print("  read      ->", f"{top['name']} {top['score']}%")
    print("  missing   ->", await storage.read("shortlists/J9999.json"))  # None, not an exception
    print("  list all  ->", await storage.list(""))
    print("  list pfx  ->", await storage.list("shortlists/"))

    await storage.delete("shortlists/J2003.json")
    print("  after del ->", await storage.list("shortlists/"), "\n")


async def demo_namespacing() -> None:
    """Namespaces give each business unit / recruiter / agent its own key space.

    This is how one deployment serves several hiring teams without one team ever
    listing another's candidates.
    """
    print("=== Namespacing ===")
    root = InMemoryStorage()

    data_bu = root.namespace("bu/data-analytics")
    platform_bu = root.namespace("bu/platform")

    await data_bu.write("open_reqs.json", b'["J2001","J2003"]')
    await platform_bu.write("open_reqs.json", b'["J2005"]')

    print("  data BU sees    :", await data_bu.list(""))
    print("  platform BU sees:", await platform_bu.list(""))
    print("  root sees       :", await root.list(""))

    # Namespaces nest.
    data_audit = data_bu.namespace("audit")
    await data_audit.write("2026-08-10.log", b"shortlist exported")
    print("  nested          :", await root.list("bu/data-analytics"), "\n")


class PiiRedactingStorage:
    """A custom backend is ~20 lines: implement write/read/delete/list.

    This one wraps another store and strips candidate email addresses before
    anything is persisted — the shape you would use for GDPR or DPDP compliance.
    A shortlist needs employee ids and scores; it does not need inboxes.
    """

    def __init__(self, inner: Storage) -> None:
        self._inner = inner

    async def write(self, key: str, data: bytes) -> None:
        try:
            payload = json.loads(data)
        except (ValueError, UnicodeDecodeError):
            await self._inner.write(key, data)
            return

        def scrub(node):
            if isinstance(node, dict):
                return {k: ("[REDACTED]" if k == "email" else scrub(v)) for k, v in node.items()}
            if isinstance(node, list):
                return [scrub(item) for item in node]
            return node

        await self._inner.write(key, json.dumps(scrub(payload)).encode())

    async def read(self, key: str) -> bytes | None:
        return await self._inner.read(key)

    async def delete(self, key: str) -> None:
        await self._inner.delete(key)

    async def list(self, query: str = "") -> list[str]:
        return await self._inner.list(query)


async def demo_custom_backend() -> None:
    print("=== Custom backend ===")
    store = PiiRedactingStorage(InMemoryStorage())
    await store.write(
        "candidates/E1002.json",
        json.dumps({"employee_id": "E1002", "email": "priya.raman@example.com", "score": 100}).encode(),
    )
    print("  stored as:", await store.read("candidates/E1002.json"))
    # Structural typing: no base class to inherit, just the four methods.
    print("  isinstance(store, Storage):", isinstance(store, Storage), "\n")


async def main() -> None:
    await demo_four_operations(InMemoryStorage(), "InMemoryStorage (tests)")
    await demo_four_operations(LocalFileStorage(str(settings.storage_dir)), "LocalFileStorage (dev)")
    await demo_namespacing()
    await demo_custom_backend()

    print("On disk:", settings.storage_dir)
    for path in sorted(settings.storage_dir.rglob("*")):
        if path.is_file():
            print("  ", path.relative_to(settings.storage_dir))


if __name__ == "__main__":
    asyncio.run(main())
