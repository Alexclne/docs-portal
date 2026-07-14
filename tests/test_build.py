"""Tests for idempotent writes and build stats."""

from pathlib import Path

import docs_portal as dp


def test_write_if_changed(tmp_path):
    p = tmp_path / "f.html"
    assert dp._write_if_changed(p, "a") == "created"
    assert dp._write_if_changed(p, "a") == "unchanged"
    assert dp._write_if_changed(p, "b") == "updated"


def test_build_stats_created_then_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(dp, "ROOT", tmp_path)
    monkeypatch.setattr(dp, "PORTAL", tmp_path / "DOCUMENTATION.html")
    monkeypatch.setattr(dp, "INCLUDE_TIMESTAMP", False)
    (tmp_path / "a.md").write_text("# A\n\ntesto\n", encoding="utf-8")

    md_files = dp.walk_files(".md")
    _docs, stats = dp.build_markdown_docs(md_files)
    assert stats == {"created": 1, "updated": 0, "unchanged": 0, "skipped": 0}

    _docs2, stats2 = dp.build_markdown_docs(md_files)
    assert stats2 == {"created": 0, "updated": 0, "unchanged": 1, "skipped": 0}


def test_build_stats_skips_non_generated_html(tmp_path, monkeypatch):
    monkeypatch.setattr(dp, "ROOT", tmp_path)
    monkeypatch.setattr(dp, "PORTAL", tmp_path / "DOCUMENTATION.html")
    monkeypatch.setattr(dp, "INCLUDE_TIMESTAMP", False)
    (tmp_path / "b.md").write_text("# B\n", encoding="utf-8")
    # Hand-written HTML without the marker should be skipped, not overwritten.
    manual = tmp_path / "b.html"
    manual.write_text("<html>hand written</html>", encoding="utf-8")

    _docs, stats = dp.build_markdown_docs(dp.walk_files(".md"))
    assert stats["skipped"] == 1
    assert manual.read_text(encoding="utf-8") == "<html>hand written</html>"


def test_walk_excludes_hidden_and_named_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(dp, "ROOT", tmp_path)
    monkeypatch.setattr(dp, "PORTAL", tmp_path / "DOCUMENTATION.html")
    (tmp_path / "keep.md").write_text("x", encoding="utf-8")
    for hidden in (".pytest_cache", ".git", "node_modules"):
        d = tmp_path / hidden
        d.mkdir()
        (d / "skip.md").write_text("x", encoding="utf-8")

    seen = {dp.rel(p).as_posix() for p in dp.walk_files(".md")}
    assert "keep.md" in seen
    assert seen == {"keep.md"}


def test_write_llms_context_creates_index(tmp_path, monkeypatch):
    monkeypatch.setattr(dp, "ROOT", tmp_path)
    monkeypatch.setattr(dp, "PORTAL", tmp_path / "DOCUMENTATION.html")
    monkeypatch.setattr(dp, "LLMS", tmp_path / "llms.txt")
    monkeypatch.setattr(dp, "INCLUDE_TIMESTAMP", False)
    monkeypatch.setattr(
        dp,
        "CONFIG",
        dp.SiteConfig(name="Acme Docs", description="Team knowledge base"),
    )

    item = dp.DocItem(
        title="Install Guide",
        path=Path("guides/install.html"),
        link=Path("guides/install.html"),
        source=Path("guides/install.md"),
        kind="Converted Markdown",
        chapter="guides",
        generated=True,
    )

    assert dp.write_llms_context([item]) == "created"
    text = (tmp_path / "llms.txt").read_text(encoding="utf-8")
    assert text.startswith("# Acme Docs\n")
    assert "> Team knowledge base" in text
    assert "<!-- ts-docs-generated: llms -->" in text
    assert "[Searchable HTML portal](DOCUMENTATION.html)" in text
    assert "- [Install Guide](guides/install.html): How-to guides and tutorials." in text
    assert dp.write_llms_context([item]) == "unchanged"


def test_write_llms_context_skips_manual_file(tmp_path, monkeypatch):
    monkeypatch.setattr(dp, "ROOT", tmp_path)
    monkeypatch.setattr(dp, "PORTAL", tmp_path / "DOCUMENTATION.html")
    monkeypatch.setattr(dp, "LLMS", tmp_path / "llms.txt")
    monkeypatch.setattr(dp, "INCLUDE_TIMESTAMP", False)

    manual = tmp_path / "llms.txt"
    manual.write_text("# Manual context\n", encoding="utf-8")

    assert dp.write_llms_context([]) == "skipped"
    assert manual.read_text(encoding="utf-8") == "# Manual context\n"


def test_write_llms_full_context_includes_document_content(tmp_path, monkeypatch):
    monkeypatch.setattr(dp, "ROOT", tmp_path)
    monkeypatch.setattr(dp, "PORTAL", tmp_path / "DOCUMENTATION.html")
    monkeypatch.setattr(dp, "LLMS_FULL", tmp_path / "llms-full.txt")
    monkeypatch.setattr(dp, "INCLUDE_TIMESTAMP", False)
    monkeypatch.setattr(
        dp,
        "CONFIG",
        dp.SiteConfig(name="Acme Docs", description="Team knowledge base"),
    )

    source = tmp_path / "guides" / "install.md"
    source.parent.mkdir()
    source.write_text("# Install\n\n```bash\nacme install\n```\n", encoding="utf-8")

    item = dp.DocItem(
        title="Install Guide",
        path=Path("guides/install.html"),
        link=Path("guides/install.html"),
        source=Path("guides/install.md"),
        kind="Converted Markdown",
        chapter="guides",
        generated=True,
    )

    assert dp.write_llms_full_context([item]) == "created"
    text = (tmp_path / "llms-full.txt").read_text(encoding="utf-8")
    assert text.startswith("# Acme Docs Full Context\n")
    assert "<!-- ts-docs-generated: llms-full -->" in text
    assert "### Install Guide" in text
    assert "- Path: `guides/install.html`" in text
    assert "- Source: `guides/install.md`" in text
    assert "# Install" in text
    assert "````text\n# Install" in text
    assert "```bash\nacme install\n```" in text
    assert dp.write_llms_full_context([item]) == "unchanged"


def test_write_llms_full_context_skips_manual_file(tmp_path, monkeypatch):
    monkeypatch.setattr(dp, "ROOT", tmp_path)
    monkeypatch.setattr(dp, "LLMS_FULL", tmp_path / "llms-full.txt")
    monkeypatch.setattr(dp, "INCLUDE_TIMESTAMP", False)

    manual = tmp_path / "llms-full.txt"
    manual.write_text("# Manual full context\n", encoding="utf-8")

    assert dp.write_llms_full_context([]) == "skipped"
    assert manual.read_text(encoding="utf-8") == "# Manual full context\n"
