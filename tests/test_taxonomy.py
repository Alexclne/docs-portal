"""Test di classificazione, riassunti, config e auto-rilevamento."""

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
    assert not dp.rule_matches(rule, "c/altro.md", "altro.md", "md")


def test_summary_matches_kind_is_and():
    rule = {"contains": ["drafts/"], "kind": "HTML originale", "text": "ok"}
    assert dp.summary_matches(rule, "drafts/f.html", "t", "HTML originale")
    assert not dp.summary_matches(rule, "drafts/f.html", "t", "Markdown convertito")


def test_item_summary_fallback_to_chapter_description():
    item = dp.DocItem(
        title="X",
        path=Path("ignoto/x.html"),
        link=Path("ignoto/x.html"),
        source=None,
        kind="HTML originale",
        chapter="other",
        generated=False,
    )
    assert dp.item_summary(item) == dp.chapter_meta("other")[1]


def test_load_config_missing_file_returns_defaults(tmp_path):
    cfg = dp.load_site_config(tmp_path / "assente.toml")
    assert cfg.name == "Documentation"
    assert cfg.chapters == ()  # vuoto -> gli accessor useranno i default


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
    (tmp_path / "vuota").mkdir()
    (tmp_path / "README.md").write_text("# home", encoding="utf-8")

    chapters, rules = dp.detect_taxonomy(tmp_path)
    keys = [key for key, _title, _desc in chapters]
    assert "overview" in keys  # documenti in radice
    assert "guide" in keys and "api" in keys
    assert "vuota" not in keys  # cartella senza documenti ignorata
    assert any(r["chapter"] == "guide" and "guide" in r["startswith"] for r in rules)
