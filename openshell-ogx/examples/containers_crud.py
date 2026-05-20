"""
Containers API CRUD test against OGX with OpenShell provider.

Tests the full lifecycle: create, list, retrieve, exec, delete.
No LLM needed; exercises the Containers API directly.

Requires:
  - A running OpenShell gateway (openshell gateway list)
  - OGX server with remote::openshell provider configured
"""

from __future__ import annotations

import httpx
import sys

BASE_URL = "http://localhost:8321/v1"


def main() -> None:
    client = httpx.Client(base_url=BASE_URL, timeout=120.0)

    # Verify server is up
    try:
        client.get("/health")
    except httpx.ConnectError:
        print("OGX server not running at", BASE_URL, file=sys.stderr)
        print("Start it with: uv run ogx run <config.yaml>", file=sys.stderr)
        sys.exit(1)

    print("=" * 60)
    print("TEST 1: Create container")
    print("=" * 60)

    resp = client.post("/containers", json={"name": "ogx-openshell-test", "memory_limit": "1g"})
    resp.raise_for_status()
    container = resp.json()
    container_id = container["id"]
    print(f"  Created:  {container_id}")
    print(f"  Name:     {container['name']}")
    print(f"  Status:   {container['status']}")

    print()
    print("=" * 60)
    print("TEST 2: List containers")
    print("=" * 60)

    resp = client.get("/containers")
    resp.raise_for_status()
    listed = resp.json()
    print(f"  Count: {len(listed['data'])}")
    for c in listed["data"]:
        print(f"    {c['id']}  name={c['name']}  status={c['status']}")

    print()
    print("=" * 60)
    print("TEST 3: Retrieve container")
    print("=" * 60)

    resp = client.get(f"/containers/{container_id}")
    resp.raise_for_status()
    retrieved = resp.json()
    print(f"  ID:     {retrieved['id']}")
    print(f"  Name:   {retrieved['name']}")
    print(f"  Status: {retrieved['status']}")

    print()
    print("=" * 60)
    print("TEST 4: Exec commands")
    print("=" * 60)

    commands = [
        ["echo hello from OpenShell"],
        ["python3 -c 'import platform; print(platform.platform())'"],
        ["uname -a"],
    ]

    for cmd_list in commands:
        resp = client.post(f"/containers/{container_id}/exec", json={
            "container_id": container_id,
            "commands": cmd_list,
        })
        resp.raise_for_status()
        result = resp.json()
        label = cmd_list[0][:50]
        print(f"  $ {label}")
        print(f"    stdout:    {result['stdout'].strip()}")
        if result["stderr"].strip():
            print(f"    stderr:    {result['stderr'].strip()}")
        print(f"    exit_code: {result['exit_code']}")
        print()

    print("=" * 60)
    print("TEST 5: Delete container")
    print("=" * 60)

    resp = client.delete(f"/containers/{container_id}")
    resp.raise_for_status()
    deleted = resp.json()
    print(f"  Deleted: {deleted['deleted']}")

    # Verify it's gone
    resp = client.get(f"/containers/{container_id}")
    assert resp.status_code == 404 or resp.status_code == 500, f"Expected 404, got {resp.status_code}"
    print(f"  Verified: container no longer retrievable")

    print()
    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
