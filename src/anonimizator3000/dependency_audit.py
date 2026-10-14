"""Report every locked path to the retained HTTP/compatibility dependencies.

Run with `uv run --locked python -m anonimizator3000.dependency_audit`.
This reports the universal lock graph, not an installed environment.
"""

from __future__ import annotations

import argparse
import json
import tomllib
from pathlib import Path

AUDITED_PACKAGES = ("httpx", "httpcore", "requests", "urllib3", "spacy-legacy")


def audit(lock_path: Path) -> dict[str, object]:
    with lock_path.open("rb") as stream:
        lock = tomllib.load(stream)
    packages = {package["name"]: package for package in lock["package"]}
    if len(packages) != len(lock["package"]):
        raise ValueError("Multiple locked versions require a source-aware audit")
    root = "anonimizator3000"
    if root not in packages:
        raise ValueError("Lockfile does not contain anonimizator3000")
    paths: dict[str, list[list[dict[str, str]]]] = {name: [] for name in AUDITED_PACKAGES}

    def walk(name: str, path: list[dict[str, str]], seen: frozenset[str]) -> None:
        package = packages[name]
        edges = [(edge, "base") for edge in package.get("dependencies", [])]
        for extra, dependencies in package.get("optional-dependencies", {}).items():
            edges.extend((edge, f"extra:{extra}") for edge in dependencies)
        for group, dependencies in package.get("dev-dependencies", {}).items():
            edges.extend((edge, f"group:{group}") for edge in dependencies)
        for edge, kind in edges:
            child = edge["name"]
            if child in seen:
                continue
            step = {"parent": name, "child": child, "kind": kind}
            if "marker" in edge:
                step["marker"] = edge["marker"]
            if "extra" in edge:
                step["extras"] = ",".join(edge["extra"])
            child_path = [*path, step]
            if child in paths and child_path not in paths[child]:
                paths[child].append(child_path)
            walk(child, child_path, seen | {child})

    walk(root, [], frozenset({root}))
    return {
        "scope": "universal lock graph; extras and markers are not activation claims",
        "locked_packages": len(packages),
        "checked": len(AUDITED_PACKAGES),
        "remaining": sum(bool(paths[name]) for name in AUDITED_PACKAGES),
        "packages": {
            name: {
                "version": packages[name]["version"] if name in packages else None,
                "source": packages[name]["source"] if name in packages else None,
                "parents": sorted({path[-1]["parent"] for path in paths[name]}),
                "paths": paths[name],
            }
            for name in AUDITED_PACKAGES
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, default=Path("uv.lock"))
    args = parser.parse_args()
    try:
        report = audit(args.lock)
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, f"Dependency audit failed: {error}\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
