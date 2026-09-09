import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Current immutable COMPAT.md row (do not mix generations).
APP_FACTORY_TAG = "v0.6.22"
MY_AUTH_TAG = "v0.5.4"
MY_USERMANAGER_TAG = "v0.6.5"


def _pyproject() -> dict[str, object]:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def _uv_sources() -> dict[str, dict[str, object]]:
    sources = _pyproject()["tool"]["uv"]["sources"]
    return {name: source for name, source in sources.items() if isinstance(source, dict)}


def _lock_packages() -> dict[str, dict[str, object]]:
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    return {package["name"]: package for package in lock["package"]}


def test_pyproject_pins_git_sources_by_tag_or_rev_not_main() -> None:
    pyproject = _pyproject()
    sources = _uv_sources()

    assert sources["app-factory"]["tag"] == APP_FACTORY_TAG
    assert sources["my-auth"]["tag"] == MY_AUTH_TAG
    assert sources["my-usermanager"]["tag"] == MY_USERMANAGER_TAG
    assert sources["posejdon"]["tag"] == "v0.1.5"
    assert sources["fala-runtime"]["rev"] == "6dd634d18b4812faed04897426bc69322ef59c34"
    assert sources["docxtor"]["tag"] == "v0.4.3"
    assert "rev" not in sources["docxtor"]
    assert "doctotext" not in sources
    assert "pdf2docx>=0.5.8" not in pyproject["project"]["dependencies"]

    for name, source in sources.items():
        assert source.get("branch") != "main", f"{name} must not track branch=main"
        assert "path" not in source, f"{name} must not use a local path pin"


def test_identity_pins_one_compat_generation() -> None:
    pyproject = _pyproject()
    sources = _uv_sources()

    for name, tag in (
        ("app-factory", APP_FACTORY_TAG),
        ("my-auth", MY_AUTH_TAG),
        ("my-usermanager", MY_USERMANAGER_TAG),
    ):
        source = sources[name]
        assert source["tag"] == tag
        assert "branch" not in source
        assert "path" not in source
        assert "rev" not in source

    assert pyproject["tool"]["uv"]["override-dependencies"] == ["app-factory[platform]"]


def test_uv_lock_matches_identity_tags() -> None:
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    packages = _lock_packages()

    assert lock["manifest"]["overrides"] == [
        {
            "name": "app-factory",
            "extras": ["platform"],
            "git": f"https://github.com/mikolaj92/app-factory?tag={APP_FACTORY_TAG}",
        }
    ]
    assert packages["app-factory"]["version"] == APP_FACTORY_TAG.removeprefix("v")
    assert packages["my-auth"]["version"] == MY_AUTH_TAG.removeprefix("v")
    assert packages["my-usermanager"]["version"] == MY_USERMANAGER_TAG.removeprefix("v")
    assert f"?tag={APP_FACTORY_TAG}#" in packages["app-factory"]["source"]["git"]
    assert f"?tag={MY_AUTH_TAG}#" in packages["my-auth"]["source"]["git"]
    assert f"?tag={MY_USERMANAGER_TAG}#" in packages["my-usermanager"]["source"]["git"]


def test_readme_documents_tag_and_rev_pins_instead_of_main() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "wskazuje branche `main`" not in readme
    assert "branch=main" not in readme
    assert "app-factory" in readme and APP_FACTORY_TAG in readme
    assert "my-auth" in readme and MY_AUTH_TAG in readme
    assert "my-usermanager" in readme and MY_USERMANAGER_TAG in readme
    assert "posejdon" in readme and "v0.1.5" in readme
    assert "fala-runtime" in readme
    assert "Docxtor" in readme and "v0.4.3" in readme
    assert "rev" in readme


def test_docs_claim_current_compat_bom() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert "current platform BOM from app-factory COMPAT.md" in pyproject
    assert APP_FACTORY_TAG in pyproject and MY_AUTH_TAG in pyproject and MY_USERMANAGER_TAG in pyproject
    assert "COMPAT.md" in readme
    assert "aktualny BOM" in readme
    assert APP_FACTORY_TAG in readme and MY_AUTH_TAG in readme and MY_USERMANAGER_TAG in readme
    assert "COMPAT.md has no v0.6.11 line" not in pyproject
    assert "COMPAT.md has no v0.6.11 line" not in readme
    assert "latest v0.6.10" not in pyproject
    assert "latest v0.6.10" not in readme
    assert "v0.6.10 / v0.4.5 / v0.5.6" not in readme
