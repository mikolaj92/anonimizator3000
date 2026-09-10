"""Keep first-party HTTP code on HTTPX2, including tests and docs."""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SURFACES = ("src", "tests")


def legacy_references(source: str) -> list[int]:
    """Catch imports, aliases, and literal dynamic imports/module shims."""
    lines: set[int] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            if any(
                alias.name.split(".")[0] == "httpx" or alias.asname == "httpx"
                for alias in node.names
            ):
                lines.add(node.lineno)
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] == "httpx" or any(
                alias.name == "httpx" or alias.asname == "httpx" for alias in node.names
            ):
                lines.add(node.lineno)
        elif isinstance(node, ast.Name) and node.id == "httpx":
            lines.add(node.lineno)
        elif isinstance(node, ast.Call) and (
            isinstance(node.func, ast.Name)
            and node.func.id == "__import__"
            or isinstance(node.func, ast.Attribute)
            and node.func.attr == "import_module"
        ) and node.args and isinstance(node.args[0], ast.Constant):
            if str(node.args[0].value).split(".")[0] == "httpx":
                lines.add(node.lineno)
        elif (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "modules"
            and isinstance(node.slice, ast.Constant)
            and node.slice.value == "httpx"
        ):
            lines.add(node.lineno)
    return sorted(lines)


def _requirement_name(requirement: str) -> str:
    name = requirement.split(";", 1)[0].strip()
    for separator in ("[", "=", ">", "<", "~", "!"):
        name = name.split(separator, 1)[0].strip()
    return name.lower()


def test_guard_rejects_legacy_imports_aliases_and_fallbacks() -> None:
    for source in (
        "import httpx",
        "import httpx as client",
        "from httpx import Client",
        "import httpx2 as httpx",
        "from httpx2 import Client as httpx",
        "try:\n import httpx2\nexcept ImportError:\n import httpx",
        'importlib.import_module("httpx")',
        'sys.modules["httpx"] = httpx2',
        "httpx = httpx2",
    ):
        assert legacy_references(source), source
    assert legacy_references("import httpx2\nhttpx2.Client()") == []
    assert legacy_references("from fastapi.testclient import TestClient") == []
    assert legacy_references("from starlette.requests import Request") == []


def test_first_party_python_has_no_legacy_client_or_shims() -> None:
    offenders: list[str] = []
    for surface in SURFACES:
        for path in (ROOT / surface).rglob("*.py"):
            if path.resolve() == Path(__file__).resolve():
                continue
            if any(part.startswith(".") for part in path.relative_to(ROOT).parts):
                continue
            lines = legacy_references(path.read_text(encoding="utf-8"))
            if lines:
                offenders.append(f"{path.relative_to(ROOT)}:{lines}")
    assert offenders == [], "Use HTTPX2 directly, not a legacy alias/fallback"


def test_first_party_manifest_does_not_declare_legacy_http_client() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = pyproject["project"]
    extras = project.get("optional-dependencies", {})
    groups = pyproject.get("dependency-groups", {})
    buckets = [
        ("project.dependencies", project.get("dependencies", [])),
        ("optional-dependencies.dev", extras.get("dev", [])),
        ("dependency-groups.dev", groups.get("dev", [])),
    ]
    for label, requirements in buckets:
        names = [_requirement_name(item) for item in requirements]
        assert "httpx" not in names, f"{label} must not restore legacy httpx"
        assert "requests" not in names, f"{label} must not restore requests"
        assert "aiohttp" not in names, f"{label} must not restore aiohttp"
        if label != "project.dependencies":
            assert "httpx2" in names, f"{label} must install HTTPX2 directly"


def test_readme_documents_httpx2_without_legacy_aliases() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "import httpx2" in readme
    assert "httpx2.*" in readme
    assert "as httpx" in readme
    assert "fastapi.testclient.TestClient" in readme
    assert "tests/test_http_client_contract.py" in readme
    assert "uv add httpx" not in readme
    assert "pip install httpx" not in readme
    assert "import httpx\n" not in readme
