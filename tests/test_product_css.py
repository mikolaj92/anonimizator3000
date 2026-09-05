from pathlib import Path


def test_product_styles_do_not_override_shell_components():
    root = Path(__file__).resolve().parents[1] / "src/anonimizator3000"
    css = (root / "static/app.css").read_text()
    for selector in ("\n.grid {", "\n.alert", "\n.card-description", "\nsvg {"):
        assert selector not in "\n" + css
    assert 'class="document-layout"' in (root / "templates/index.html").read_text()
