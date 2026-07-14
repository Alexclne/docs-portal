"""Tests for Markdown -> HTML rendering with markdown-it-py."""

from pathlib import Path

import docs_portal as dp


def render(md: str, source: Path = Path("doc.md")) -> str:
    return dp.render_markdown(md, source)[0]


def test_nested_list_is_actually_nested():
    html = render("- a\n  - b\n")
    # A nested list: two <ul> elements, with the second inside a <li>.
    assert html.count("<ul>") == 2
    assert "<ul>" in html[html.index("a") :]


def test_task_list_becomes_checkbox():
    html = render("- [ ] todo\n- [x] done\n")
    assert 'type="checkbox"' in html
    assert "[ ]" not in html and "[x]" not in html


def test_table():
    html = render("| a | b |\n| - | - |\n| 1 | 2 |\n")
    assert "<table>" in html
    assert "<th>a</th>" in html
    assert "<td>1</td>" in html


def test_fenced_code_keeps_language_class():
    html = render("```python\nx = 1\n```\n")
    assert "<pre><code" in html
    assert "language-python" in html


def test_inline_code_and_emphasis():
    html = render("Text with `code`, **bold** and *italic*.")
    assert "<code>code</code>" in html
    assert "<strong>bold</strong>" in html
    assert "<em>italic</em>" in html


def test_headings_produce_toc_and_anchors():
    html, toc = dp.render_markdown("# One\n## Two\n### Three\n", Path("d.md"))
    assert [level for level, _id, _t in toc] == [1, 2, 3]
    for _level, anchor, _text in toc:
        assert f'id="{anchor}"' in html


def test_duplicate_headings_get_unique_ids():
    _html, toc = dp.render_markdown("# Test\n# Test\n", Path("d.md"))
    ids = [anchor for _l, anchor, _t in toc]
    assert len(ids) == len(set(ids))


def test_image_is_rendered():
    html = render("![description](pic.png)")
    assert "<img" in html
    assert 'src="pic.png"' in html
    assert 'alt="description"' in html


def test_relative_md_link_rewritten_to_html(tmp_path, monkeypatch):
    monkeypatch.setattr(dp, "ROOT", tmp_path)
    (tmp_path / "other.md").write_text("x", encoding="utf-8")
    html = render("[see](other.md)", tmp_path / "doc.md")
    assert 'href="other.html"' in html


def test_external_link_is_untouched():
    html = render("[site](https://example.com/x)")
    assert 'href="https://example.com/x"' in html


def test_raw_html_is_neutralized():
    html = render("<script>alert(1)</script>\n")
    assert "<script>" not in html
