"""Tests for classification, summaries, config and auto-detection."""

from pathlib import Path

import docs_portal as dp


def test_chapter_for_defaults():
    assert dp.chapter_for(Path("guides/x.md"), "md") == "guides"
    assert dp.chapter_for(Path("api/x.md"), "md") == "reference"
    assert dp.chapter_for(Path("readme.md"), "md") == "overview"
    assert dp.chapter_for(Path("something/unknown.md"), "md") == "other"


def test_rule_matches_operators():
    rule = {"chapter": "x", "startswith": ["a/"], "contains": ["z"], "name_in": ["r.md"]}
    assert dp.rule_matches(rule, "a/b.md", "b.md", "md")
    assert dp.rule_matches(rule, "c/zeta.md", "zeta.md", "md")
    assert dp.rule_matches(rule, "c/r.md", "r.md", "md")
    assert not dp.rule_matches(rule, "c/other.md", "other.md", "md")


def test_summary_matches_kind_is_and():
    rule = {"contains": ["drafts/"], "kind": "Original HTML", "text": "ok"}
    assert dp.summary_matches(rule, "drafts/f.html", "t", "Original HTML")
    assert not dp.summary_matches(rule, "drafts/f.html", "t", "Converted Markdown")


def test_item_summary_fallback_to_chapter_description():
    item = dp.DocItem(
        title="X",
        path=Path("unknown/x.html"),
        link=Path("unknown/x.html"),
        source=None,
        kind="Original HTML",
        chapter="other",
        generated=False,
    )
    assert dp.item_summary(item) == dp.chapter_meta("other")[1]


def test_load_config_missing_file_returns_defaults(tmp_path):
    cfg = dp.load_site_config(tmp_path / "assente.toml")
    assert cfg.name == "Documentation"
    assert cfg.chapters == ()  # empty -> accessors use the defaults


def test_full_example_reproduces_defaults():
    example = Path(__file__).resolve().parent.parent / "examples" / "docs-portal.full-example.toml"
    cfg = dp.load_site_config(example)
    assert list(cfg.chapters) == [tuple(c) for c in dp.CHAPTERS]
    assert list(cfg.rules) == list(dp.DEFAULT_RULES)
    assert cfg.open_chapters == dp.DEFAULT_OPEN_CHAPTERS


def test_detect_taxonomy(tmp_path):
    (tmp_path / "Guide").mkdir()
    (tmp_path / "Guide" / "a.md").write_text("# a", encoding="utf-8")
    (tmp_path / "API").mkdir()
    (tmp_path / "API" / "b.html").write_text("<h1>b</h1>", encoding="utf-8")
    (tmp_path / "empty").mkdir()
    (tmp_path / "README.md").write_text("# home", encoding="utf-8")

    chapters, rules = dp.detect_taxonomy(tmp_path)
    keys = [key for key, _title, _desc in chapters]
    assert "overview" in keys  # documents in the root
    assert "guide" in keys and "api" in keys
    assert "empty" not in keys  # folder without documents is ignored
    assert any(r["chapter"] == "guide" and "guide" in r["startswith"] for r in rules)
