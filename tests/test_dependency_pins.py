from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parents[1]


def _uv_sources() -> dict[str, dict[str, object]]:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    sources = pyproject["tool"]["uv"]["sources"]
    return {name: source for name, source in sources.items() if isinstance(source, dict)}


def test_pyproject_pins_git_sources_by_tag_or_rev_not_main() -> None:
    sources = _uv_sources()

    assert sources["app-factory"]["tag"] == "v0.6.5"
    assert sources["my-auth"]["tag"] == "v0.4.2"
    assert sources["posejdon"]["tag"] == "v0.1.0"
    assert sources["fala-runtime"]["rev"] == "6dd634d18b4812faed04897426bc69322ef59c34"
    assert sources["doctotext"]["rev"] == "33aaf6071f45cae8f33ad97fe4b40ffd0f1fd18a"

    for name, source in sources.items():
        assert source.get("branch") != "main", f"{name} must not track branch=main"


def test_readme_documents_tag_and_rev_pins_instead_of_main() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "wskazuje branche `main`" not in readme
    assert "branch=main" not in readme
    assert "app-factory" in readme and "v0.6.5" in readme
    assert "my-auth" in readme and "v0.4.2" in readme
    assert "posejdon" in readme and "v0.1.0" in readme
    assert "fala-runtime" in readme
    assert "doctotext" in readme
    assert "rev" in readme
