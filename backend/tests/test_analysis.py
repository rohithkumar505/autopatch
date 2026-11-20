from __future__ import annotations

from pathlib import Path

from autopatch.analysis.base import analyzer_for, detect_languages
from autopatch.analysis.findings import issues_to_drafts, rank


def _write(root: Path, files: dict[str, str]) -> list[str]:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    return sorted(files)


def test_detect_languages():
    assert detect_languages(["a.py", "b.py", "web/app.js", "README.md"]) == {"Python": 2, "JavaScript": 1, "Markdown": 1}


def test_structure_imports_calls_and_tests(tmp_path):
    files = _write(tmp_path, {
        "shop/__init__.py": "",
        "shop/pricing.py": "def discount(p, pct):\n    return p - p * pct\n\ndef _round(x):\n    return round(x, 2)\n",
        "shop/cart.py": (
            "from .pricing import discount\nfrom . import pricing\nimport requests\n\n"
            "class Cart:\n    def __init__(self):\n        self.items = []\n"
            "    def total(self):\n        return pricing._round(sum(discount(i, 0.1) for i in self.items))\n"
            "    def checkout(self):\n        return self.total()\n"
        ),
        "tests/test_cart.py": "from shop.cart import Cart\n\ndef test_total():\n    assert Cart().total() == 0\n",
        "broken.py": "def oops(:\n    pass\n",
        "requirements.txt": "requests>=2\n# comment\nflask==3.0\n",
    })
    st = analyzer_for("Python").structure(tmp_path, files)
    cart = st.modules["shop/cart.py"]
    assert cart.imports == ["shop/pricing.py"]
    assert cart.external_imports == ["requests"]
    assert "shop/cart.py" in st.modules["shop/pricing.py"].imported_by
    total = st.symbols["shop.cart.Cart.total"]
    assert set(total.calls) == {"shop.pricing.discount", "shop.pricing._round"}
    assert "shop.cart.Cart.checkout" in total.called_by
    assert total.tested_by == ["tests.test_cart.test_total"]
    assert st.declared_dependencies == ["flask", "requests"]
    assert st.syntax_issues and st.syntax_issues[0].file == "broken.py" and st.syntax_issues[0].confirmed
    assert st.symbol_at("shop/cart.py", 9).qualname == "shop.cart.Cart.total"


def test_static_tools_find_seeded_sample_bugs(sample_copy):
    analyzer = analyzer_for("Python")
    files = ["grades.py", "tests/test_grades.py"]
    st = analyzer.structure(sample_copy, files)
    issues, stats = analyzer.static_issues(sample_copy, files, {"ruff", "pylint"})
    assert stats["ruff"]["status"] == "ok" and stats["pylint"]["status"] == "ok"
    concepts = {(i.concept, i.tool) for i in issues}
    assert ("eval", "ruff") in concepts and ("eval", "pylint") in concepts
    assert ("mutable-default", "ruff") in concepts and ("mutable-default", "pylint") in concepts

    drafts = rank(issues_to_drafts(issues, st, lambda f: (sample_copy / f).read_text()), st)
    by_func = {d.function: d for d in drafts}
    # Ruff and Pylint both flag eval(); they must merge into one finding with both sources.
    ev = by_func["parse_marks"]
    assert ev.category == "security" and {s.split(":")[0] for s in ev.sources} == {"ruff", "pylint"}
    assert "eval" in ev.code_snippet and ev.snippet_start == 7
    assert by_func["add_marks"].category == "state_corruption"
    assert drafts[0].function == "parse_marks"  # high-severity security bug ranks first
    assert "no existing test" in by_func["add_marks"].impact or "covered by" in by_func["add_marks"].impact
