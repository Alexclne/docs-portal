#!/usr/bin/env python3
"""docs-portal: build a static HTML documentation portal from a Markdown folder.

Markdown rendering is delegated to markdown-it-py (CommonMark). Commands:
  docs-portal init    create docs-portal.toml from the detected folders
  docs-portal build   generate HTML output (the default command)
"""

from __future__ import annotations

import argparse
import base64
import html
import json
import mimetypes
import re
import sys
import tomllib
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Iterable
from urllib.parse import quote

from markdown_it import MarkdownIt
from mdit_py_plugins.tasklists import tasklists_plugin


# Default scan root: the current working directory. Overridden by --root.
ROOT = Path.cwd()
PORTAL = ROOT / "DOCUMENTATION.html"
LLMS = ROOT / "llms.txt"
LLMS_FULL = ROOT / "llms-full.txt"
GENERATED_MARKER = "ts-docs-generated"
STANDARDIZED_MARKER = "ts-docs-ui-standardized"

# Disabled by --no-timestamp so repeated builds can be byte-identical.
INCLUDE_TIMESTAMP = True

DEFAULT_DESCRIPTION = (
    "Guides, references and notes collected into one navigable, searchable index."
)

# Document classification rules. First match wins.
# A rule assigns a chapter when any path/name condition matches.
DEFAULT_RULES = [
    {"chapter": "guides", "startswith": ["guides/", "guide/", "tutorials/", "howto/", "how-to/"]},
    {"chapter": "reference", "startswith": ["reference/", "api/", "spec/"]},
    {"chapter": "overview", "name_in": ["readme.md", "index.md", "overview.md"], "startswith": ["docs/"]},
]

# Per-document summaries. First match wins.
DEFAULT_SUMMARIES: list[dict] = []

# Chapters expanded by default in the portal.
DEFAULT_OPEN_CHAPTERS = frozenset({"overview"})


@dataclass(frozen=True)
class SiteConfig:
    """Branding, theme and taxonomy options loaded from docs-portal.toml."""

    name: str = "Documentation"
    tagline: str = "Knowledge base"
    description: str = DEFAULT_DESCRIPTION
    doc_title_suffix: str = "Docs"
    logo_data_uri: str = ""
    favicon_href: str = ""
    footer: str = ""
    color_overrides: dict[str, str] = field(default_factory=dict)
    # Empty values use the built-in defaults.
    chapters: tuple = field(default_factory=tuple)
    rules: tuple = field(default_factory=tuple)
    quick_links: tuple = field(default_factory=tuple)
    manual_docs: tuple = field(default_factory=tuple)
    summaries: tuple = field(default_factory=tuple)
    open_chapters: frozenset = field(default_factory=frozenset)


# Riassegnato in main() dopo aver caricato docs-portal.toml (se presente).
CONFIG = SiteConfig()


def cfg_chapters() -> list:
    return list(CONFIG.chapters) if CONFIG.chapters else CHAPTERS


def cfg_rules() -> list:
    return list(CONFIG.rules) if CONFIG.rules else DEFAULT_RULES


def cfg_quick_links() -> list:
    return list(CONFIG.quick_links) if CONFIG.quick_links else QUICK_LINKS


def cfg_manual_docs() -> list:
    if CONFIG.manual_docs:
        return list(CONFIG.manual_docs)
    return [p.as_posix() for p in MANUAL_DOC_HTML]


def cfg_summaries() -> list:
    return list(CONFIG.summaries) if CONFIG.summaries else DEFAULT_SUMMARIES


def cfg_open_chapters() -> frozenset:
    return CONFIG.open_chapters or DEFAULT_OPEN_CHAPTERS

MANUAL_DOC_HTML: list[Path] = []

EXCLUDED_DIRS = {
    ".git",
    ".codex",
    ".claude",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    "dist",
    "build",
    "tools",
    "docs-portal",
}

CHAPTERS = [
    ("overview", "01. Overview", "Project overview and top-level notes."),
    ("guides", "02. Guides", "How-to guides and tutorials."),
    ("reference", "03. Reference", "Reference material and specifications."),
    ("other", "99. Other", "Uncategorized documents."),
]


@dataclass(frozen=True)
class DocItem:
    title: str
    path: Path
    link: Path
    source: Path | None
    kind: str
    chapter: str
    generated: bool


def walk_files(suffix: str) -> list[Path]:
    files: list[Path] = []
    for path in ROOT.rglob(f"*{suffix}"):
        rel_parts = path.relative_to(ROOT).parts
        if any(part in EXCLUDED_DIRS for part in rel_parts):
            continue
        # Skip hidden directories (.git, .pytest_cache, .venv, ...).
        if any(part.startswith(".") for part in rel_parts[:-1]):
            continue
        if path == PORTAL:
            continue
        files.append(path)
    return sorted(files, key=lambda p: p.relative_to(ROOT).as_posix().lower())


def rel(path: Path) -> Path:
    return path.relative_to(ROOT)


def rel_link(target: Path, current_dir: Path) -> str:
    value = Path(target).resolve().relative_to(ROOT)
    current = current_dir.resolve().relative_to(ROOT) if current_dir.resolve() != ROOT else Path(".")
    if current == Path("."):
        return value.as_posix()
    return Path(*([".."] * len(current.parts)), value).as_posix()


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def generated_stamp() -> str:
    """Build timestamp for generated files, or "" when --no-timestamp is used."""
    if INCLUDE_TIMESTAMP:
        return datetime.now().strftime("%Y-%m-%d %H:%M")
    return ""


def embed_image(path: Path) -> str:
    """Encode an image as a base64 data URI so HTML output stays self-contained."""
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{data}"


def build_favicon(value: str, base_dir: Path) -> str:
    """Build a favicon from an image path or from an emoji rendered as inline SVG."""
    candidate = (base_dir / value).resolve()
    if candidate.is_file():
        return embed_image(candidate)
    emoji = html.escape(value.strip(), quote=True)
    svg = (
        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'>"
        f"<text y='.9em' font-size='90'>{emoji}</text></svg>"
    )
    return "data:image/svg+xml," + quote(svg)


def load_site_config(path: Path | None) -> SiteConfig:
    """Load docs-portal.toml, or use defaults when no config exists."""
    defaults = SiteConfig()
    if path is None or not path.exists():
        return defaults

    data = tomllib.loads(read_text(path))
    branding = data.get("branding", {})
    raw_colors = data.get("colors", {})

    logo_uri = ""
    logo_rel = branding.get("logo")
    if logo_rel:
        logo_path = (path.parent / logo_rel).resolve()
        if logo_path.is_file():
            logo_uri = embed_image(logo_path)
        else:
            print(f"[warning] logo not found: {logo_path}")

    favicon_value = branding.get("favicon", "")
    favicon_href = build_favicon(favicon_value, path.parent) if favicon_value else ""

    colors: dict[str, str] = {}
    for key, value in raw_colors.items():
        clean_key = re.sub(r"[^a-z0-9-]", "", str(key).lower())
        clean_value = re.sub(r"[<>{};]", "", str(value)).strip()
        if clean_key and clean_value:
            colors[clean_key] = clean_value

    # Omitted taxonomy sections stay empty so cfg_* accessors use built-in defaults.
    chapters = tuple(
        (c["key"], c.get("title", c["key"]), c.get("description", ""))
        for c in data.get("chapters", [])
    )
    rules = tuple(data.get("rules", []))
    quick_links = tuple(
        (q["path"], q.get("label", ""), q.get("description", ""), q.get("area", ""))
        for q in data.get("quick_links", [])
    )
    manual_docs = tuple(data.get("manual_docs", []))
    summaries = tuple(data.get("summaries", []))
    open_chapters = frozenset(data.get("open_chapters", []))

    return SiteConfig(
        name=branding.get("name", defaults.name),
        tagline=branding.get("tagline", defaults.tagline),
        description=branding.get("description", defaults.description),
        doc_title_suffix=branding.get(
            "doc_title_suffix", branding.get("name", defaults.doc_title_suffix)
        ),
        logo_data_uri=logo_uri,
        favicon_href=favicon_href,
        footer=branding.get("footer", ""),
        color_overrides=colors,
        chapters=chapters,
        rules=rules,
        quick_links=quick_links,
        manual_docs=manual_docs,
        summaries=summaries,
        open_chapters=open_chapters,
    )


def render_theme_css(config: SiteConfig) -> str:
    """CSS supplementare per colori/logo/footer; "" se non c'e' personalizzazione."""
    parts: list[str] = []
    if config.color_overrides:
        decls = " ".join(f"--{k}: {v};" for k, v in config.color_overrides.items())
        parts.append(f":root {{ {decls} }}")
    if config.logo_data_uri:
        parts.append(
            ".brand-logo { height: 30px; width: auto; vertical-align: middle;"
            " margin-right: 10px; }"
        )
        parts.append(
            "body.doc-page > nav .doc-logo { display: block; max-width: 160px;"
            " height: auto; margin-bottom: 14px; }"
        )
    if config.footer:
        parts.append(
            ".site-footer { max-width: 1240px; margin: 0 auto;"
            " padding: 24px 20px 48px; color: var(--muted); font-size: 13px;"
            " border-top: 1px solid var(--line); }"
        )
    if not parts:
        return ""
    return "\n" + "\n".join(parts) + "\n"


def title_from_markdown(text: str, fallback: str) -> str:
    for line in text.splitlines():
        match = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", line)
        if match:
            return strip_inline(match.group(1))
    return prettify_filename(fallback)


def title_from_html(path: Path) -> str:
    text = read_text(path)
    match = re.search(r"<title[^>]*>(.*?)</title>", text, flags=re.I | re.S)
    if match:
        return html.unescape(re.sub(r"\s+", " ", match.group(1)).strip())
    match = re.search(r"<h1[^>]*>(.*?)</h1>", text, flags=re.I | re.S)
    if match:
        cleaned = re.sub(r"<[^>]+>", "", match.group(1))
        return html.unescape(re.sub(r"\s+", " ", cleaned).strip())
    return prettify_filename(path.stem)


def strip_html_text(text: str) -> str:
    text = re.sub(r"<script\b[^>]*>.*?</script>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_search_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def strip_inline(text: str) -> str:
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    return text.strip()


def prettify_filename(name: str) -> str:
    name = re.sub(r"[-_]+", " ", name)
    return name.strip().title() or "Documento"


def slugify(text: str, used: set[str]) -> str:
    base = strip_inline(text).lower()
    base = re.sub(r"[^a-z0-9]+", "-", base)
    base = base.strip("-") or "section"
    slug = base
    counter = 2
    while slug in used:
        slug = f"{base}-{counter}"
        counter += 1
    used.add(slug)
    return slug


def convert_markdown_link(href: str, source: Path) -> str:
    if re.match(r"^(https?:|mailto:|tel:|#)", href):
        return href
    if href.startswith("<") and href.endswith(">"):
        href = href[1:-1]

    anchor = ""
    if "#" in href:
        href, anchor = href.split("#", 1)
        anchor = f"#{anchor}"

    if href.lower().endswith(".md"):
        target = (source.parent / href).resolve()
        if target.is_relative_to(ROOT):
            html_target = target.with_suffix(".html")
            href = rel_link(html_target, source.parent)

    return href + anchor


@lru_cache(maxsize=1)
def _markdown_parser() -> MarkdownIt:
    """Parser CommonMark (markdown-it-py) con tabelle GFM, strikethrough e task list.

    html=False: l'HTML grezzo nel Markdown viene neutralizzato (nessuna injection).
    """
    md = MarkdownIt("commonmark", {"html": False, "typographer": False})
    md.enable(["table", "strikethrough"])
    md.use(tasklists_plugin, enabled=True, label=True)
    return md


def _rewrite_relative_links(tokens: list, source: Path) -> None:
    """Riscrive gli href relativi .md -> .html direttamente sui token inline."""
    for token in tokens:
        if token.type != "inline" or not token.children:
            continue
        for child in token.children:
            if child.type == "link_open":
                href = child.attrGet("href")
                if href:
                    child.attrSet("href", convert_markdown_link(href, source))


def render_markdown(text: str, source: Path) -> tuple[str, list[tuple[int, str, str]]]:
    """Converte Markdown in HTML e restituisce (html, indice) con markdown-it-py.

    L'indice e' la lista di (livello, id, testo) delle intestazioni; gli id univoci
    sono applicati come ancore sulle <h*>.
    """
    md = _markdown_parser()
    env: dict = {}
    tokens = md.parse(text, env)

    toc: list[tuple[int, str, str]] = []
    used_ids: set[str] = set()
    for index, token in enumerate(tokens):
        if token.type != "heading_open":
            continue
        level = int(token.tag[1:])
        inline = tokens[index + 1] if index + 1 < len(tokens) else None
        raw_title = inline.content if inline is not None else ""
        heading_id = slugify(raw_title, used_ids)
        token.attrSet("id", heading_id)
        toc.append((level, heading_id, strip_inline(raw_title)))

    _rewrite_relative_links(tokens, source)
    return md.renderer.render(tokens, md.options, env), toc


def rule_matches(rule: dict, value: str, name: str, kind: str) -> bool:
    if any(value.startswith(prefix) for prefix in rule.get("startswith", [])):
        return True
    if any(needle in value for needle in rule.get("contains", [])):
        return True
    if name in rule.get("name_in", []):
        return True
    if rule.get("kind") == "html" and kind == "html":
        return True
    return False


def chapter_for(path: Path, kind: str) -> str:
    value = path.as_posix().lower()
    name = path.name.lower()
    for rule in cfg_rules():
        if rule_matches(rule, value, name, kind):
            return rule["chapter"]
    return "other"


@lru_cache(maxsize=1)
def common_css() -> str:
    """Shared stylesheet packaged as docs_portal.css."""
    return (files(__package__) / "docs_portal.css").read_text(encoding="utf-8")


def render_document_page(md_path: Path, html_path: Path) -> tuple[DocItem, str]:
    text = read_text(md_path)
    title = title_from_markdown(text, md_path.stem)
    body, toc = render_markdown(text, md_path)
    rel_md = rel(md_path)
    rel_html = rel(html_path)
    portal_href = rel_link(PORTAL, html_path.parent)
    source_href = md_path.name
    generated_at = generated_stamp()

    toc_html = "\n".join(
        f'<a class="toc-l{level}" href="#{anchor}">{html.escape(label)}</a>'
        for level, anchor, label in toc
        if level <= 4
    )
    if not toc_html:
        toc_html = '<span class="pill">No page outline</span>'

    cfg = CONFIG
    favicon_link = (
        f'\n  <link rel="icon" href="{cfg.favicon_href}">' if cfg.favicon_href else ""
    )
    theme_css = render_theme_css(cfg)
    doc_logo = (
        f'<img class="doc-logo" src="{cfg.logo_data_uri}"'
        f' alt="{html.escape(cfg.name, quote=True)}">'
        if cfg.logo_data_uri
        else ""
    )

    page = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)} - {html.escape(cfg.doc_title_suffix, quote=False)}</title>{favicon_link}
  <style>{common_css()}{theme_css}</style>
</head>
<body class="doc-page">
  <!-- {GENERATED_MARKER}: source={rel_md.as_posix()} generated={generated_at} -->
  <nav>
    {doc_logo}<strong>{html.escape(title)}<br><span class="small">Markdown document</span></strong>
    <a class="nav-back" href="{portal_href}">Portal index</a>
    <a href="{html.escape(source_href)}">Markdown source</a>
    <div class="group">On this page</div>
      {toc_html}
  </nav>
  <main>
    <p class="subtitle">Source: <code>{html.escape(rel_md.as_posix())}</code></p>
    {body}
  </main>
</body>
</html>
"""
    status = _write_if_changed(html_path, page)
    item = DocItem(
        title=title,
        path=rel_html,
        link=rel_html,
        source=rel_md,
        kind="Converted Markdown",
        chapter=chapter_for(rel_md, "md"),
        generated=True,
    )
    return item, status


def _write_if_changed(path: Path, content: str) -> str:
    """Write a file only when its content changed.

    Returns "created", "updated" or "unchanged". Avoiding unnecessary rewrites
    keeps mtimes and Git diffs clean.
    """
    if path.exists():
        try:
            if read_text(path) == content:
                return "unchanged"
        except OSError:
            pass
        path.write_text(content, encoding="utf-8")
        return "updated"
    path.write_text(content, encoding="utf-8")
    return "created"


def can_write_generated_html(path: Path) -> bool:
    if not path.exists():
        return True
    try:
        return GENERATED_MARKER in read_text(path)
    except OSError:
        return False


def build_markdown_docs(md_files: Iterable[Path]) -> tuple[list[DocItem], dict[str, int]]:
    docs: list[DocItem] = []
    stats = {"created": 0, "updated": 0, "unchanged": 0, "skipped": 0}
    for md_path in md_files:
        html_path = md_path.with_suffix(".html")
        if can_write_generated_html(html_path):
            item, status = render_document_page(md_path, html_path)
            docs.append(item)
            stats[status] += 1
        else:
            text = read_text(md_path)
            docs.append(
                DocItem(
                    title=title_from_markdown(text, md_path.stem),
                    path=rel(html_path),
                    link=rel(html_path),
                    source=rel(md_path),
                    kind="Existing HTML",
                    chapter=chapter_for(rel(md_path), "md"),
                    generated=False,
                )
            )
            stats["skipped"] += 1
    return docs, stats


def collect_original_html(generated_links: set[Path]) -> list[DocItem]:
    docs: list[DocItem] = []
    for path in walk_files(".html"):
        rel_path = rel(path)
        if rel_path in generated_links or path == PORTAL:
            continue
        if GENERATED_MARKER in read_text(path):
            continue
        docs.append(
            DocItem(
                title=title_from_html(path),
                path=rel_path,
                link=rel_path,
                source=None,
                kind="Original HTML",
                chapter=chapter_for(rel_path, "html"),
                generated=False,
            )
        )
    return docs


def add_body_class(text: str, class_name: str) -> str:
    match = re.search(r"<body([^>]*)>", text, flags=re.I)
    if not match:
        return text

    attrs = match.group(1)
    class_match = re.search(r'class=(["\'])(.*?)\1', attrs, flags=re.I | re.S)

    if class_match:
        classes = class_match.group(2).split()
        if class_name in classes:
            return text
        new_classes = " ".join([*classes, class_name])
        new_attrs = (
            attrs[: class_match.start()]
            + f'class={class_match.group(1)}{new_classes}{class_match.group(1)}'
            + attrs[class_match.end() :]
        )
    else:
        new_attrs = f'{attrs} class="{class_name}"'

    return text[: match.start()] + f"<body{new_attrs}>" + text[match.end() :]


def standardize_manual_doc_ui() -> tuple[int, int]:
    checked = 0
    updated = 0
    replacement = f"<style>\n{common_css()}\n</style>"

    for rel_str in cfg_manual_docs():
        path = ROOT / rel_str
        if not path.exists():
            continue

        original = read_text(path)
        if not re.search(r"<style>.*?</style>", original, flags=re.I | re.S):
            continue

        checked += 1
        text = re.sub(r"<style>.*?</style>", replacement, original, count=1, flags=re.I | re.S)
        text = add_body_class(text, "doc-page")

        if STANDARDIZED_MARKER not in text:
            text = re.sub(r"(<body[^>]*>)", rf"<!-- {STANDARDIZED_MARKER} -->\n\1", text, count=1, flags=re.I)

        if text != original:
            path.write_text(text, encoding="utf-8")
            updated += 1

    return checked, updated


QUICK_LINKS: list[tuple[str, str, str, str]] = []


def chapter_meta(chapter: str) -> tuple[str, str]:
    for key, title, desc in cfg_chapters():
        if key == chapter:
            return title, desc
    return "Other", "Documents that were not classified automatically."


def doc_type(item: DocItem) -> str:
    if item.generated:
        return "markdown"
    if item.path.as_posix().lower() in {s.lower() for s in cfg_manual_docs()}:
        return "manual"
    return "html"


def doc_type_label(value: str) -> str:
    return {
        "markdown": "Markdown",
        "manual": "Manual guide",
        "html": "Existing HTML",
    }.get(value, value)


def summary_matches(rule: dict, value: str, title: str, kind: str) -> bool:
    if "contains" in rule or "endswith" in rule:
        path_ok = any(needle in value for needle in rule.get("contains", [])) or any(
            value.endswith(suffix) for suffix in rule.get("endswith", [])
        )
        if not path_ok:
            return False
    if "title_contains" in rule and not any(t in title for t in rule["title_contains"]):
        return False
    if "kind" in rule and rule["kind"] != kind:
        return False
    return True


def item_summary(item: DocItem) -> str:
    value = item.path.as_posix().lower()
    title = item.title.lower()
    for rule in cfg_summaries():
        if summary_matches(rule, value, title, item.kind):
            return rule["text"]
    return chapter_meta(item.chapter)[1]


def doc_dom_id(item: DocItem) -> str:
    value = item.path.as_posix().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value or "doc"


def item_content_text(item: DocItem) -> str:
    if item.source is not None:
        source_path = ROOT / item.source
        if source_path.exists():
            return read_text(source_path)

    html_path = ROOT / item.path
    if html_path.exists():
        return strip_html_text(read_text(html_path))

    return ""


def search_index_entry(item: DocItem) -> dict[str, str]:
    chapter_title = chapter_meta(item.chapter)[0]
    summary = item_summary(item)
    content = item_content_text(item)
    source = item.source.as_posix() if item.source else ""
    search_blob = " ".join(
        [
            item.title,
            item.path.as_posix(),
            source,
            doc_type_label(doc_type(item)),
            chapter_title,
            summary,
            content,
        ]
    )
    preview = re.sub(r"\s+", " ", strip_html_text(content) if "<" in content else content).strip()
    return {
        "id": doc_dom_id(item),
        "title": item.title,
        "path": item.path.as_posix(),
        "type": doc_type(item),
        "chapter": chapter_title,
        "summary": summary,
        "searchText": normalize_search_text(search_blob)[:120000],
        "preview": preview[:120000],
    }


def card_html(item: DocItem) -> str:
    source = ""
    if item.source is not None:
        source = f'<a class="button secondary" href="{html.escape(item.source.as_posix())}">MD source</a>'
    type_key = doc_type(item)
    type_label = doc_type_label(type_key)
    chapter_title = chapter_meta(item.chapter)[0]
    search_text = " ".join(
        [
            item.title,
            item.path.as_posix(),
            item.kind,
            type_label,
            chapter_title,
            item_summary(item),
            item.source.as_posix() if item.source else "",
        ]
    )
    doc_id = doc_dom_id(item)
    return f"""
<article class="doc-card" id="{html.escape(doc_id, quote=True)}" data-doc-id="{html.escape(doc_id, quote=True)}" data-type="{type_key}" data-search="{html.escape(search_text.lower(), quote=True)}">
  <div>
    <p class="doc-meta"><span class="pill">{html.escape(type_label)}</span> <span class="pill info">{html.escape(chapter_title)}</span></p>
    <h3>{html.escape(item.title)}</h3>
    <p class="doc-summary">{html.escape(item_summary(item))}</p>
    <p class="doc-hit" hidden></p>
    <p class="doc-path">{html.escape(item.path.as_posix())}</p>
  </div>
  <div class="actions">
    <a class="button" href="{html.escape(item.link.as_posix())}">Open HTML</a>
    {source}
  </div>
</article>
"""


def quick_card_html(item: DocItem, label: str, description: str, area: str) -> str:
    return f"""
<a class="quick-card" href="{html.escape(item.link.as_posix())}">
  <span class="pill info">{html.escape(area)}</span>
  <h3>{html.escape(label)}</h3>
  <p>{html.escape(description)}</p>
  <code>{html.escape(item.path.as_posix())}</code>
</a>
"""


def chapter_card_html(index: int, key: str, title: str, desc: str, count: int) -> str:
    short_title = title.split(". ", 1)[-1]
    doc_word = "document" if count == 1 else "documents"
    return f"""
<a class="area-card" href="#{html.escape(key, quote=True)}">
  <span class="area-index">{index:02d}</span>
  <strong>{html.escape(short_title)}</strong>
  <p>{html.escape(desc)}</p>
  <span class="area-meta">{count} {doc_word}</span>
</a>
"""


def write_portal(items: list[DocItem], md_count: int) -> str:
    chapter_map = {key: (title, desc, []) for key, title, desc in cfg_chapters()}
    for item in sorted(items, key=lambda d: (d.chapter, d.path.as_posix().lower())):
        if item.chapter not in chapter_map:
            item = DocItem(
                title=item.title,
                path=item.path,
                link=item.link,
                source=item.source,
                kind=item.kind,
                chapter="other",
                generated=item.generated,
            )
        chapter_map[item.chapter][2].append(item)

    item_by_path = {item.path.as_posix().lower(): item for item in items}
    quick_cards = []
    for path, title, desc, area in cfg_quick_links():
        item = item_by_path.get(path.lower())
        if item:
            quick_cards.append(quick_card_html(item, title, desc, area))

    type_counts = {
        "markdown": sum(1 for item in items if doc_type(item) == "markdown"),
        "manual": sum(1 for item in items if doc_type(item) == "manual"),
        "html": sum(1 for item in items if doc_type(item) == "html"),
    }

    nav = "\n".join(
        [
            '<a href="#areas">Documentation areas</a>',
            '<a href="#start">Quick access</a>',
            '<a href="#catalog">Full catalog</a>',
        ]
        + [
            f'<a href="#{key}">{html.escape(title)}</a>'
            for key, title, _desc in cfg_chapters()
            if chapter_map[key][2]
        ]
    )

    open_chapters = cfg_open_chapters()

    sections: list[str] = []
    for key, title, desc in cfg_chapters():
        docs = chapter_map[key][2]
        if not docs:
            continue
        doc_word = "document" if len(docs) == 1 else "documents"
        cards = "\n".join(card_html(item) for item in docs)
        open_attr = " open" if key in open_chapters else ""
        sections.append(
            f"""
<details class="chapter" id="{key}"{open_attr}>
  <summary>
    <span>
      <h2>{html.escape(title)}</h2>
      <p>{html.escape(desc)} {len(docs)} {doc_word}.</p>
    </span>
    <span class="chapter-count">{len(docs)}</span>
  </summary>
  <div class="doc-grid">
    {cards}
  </div>
</details>
"""
        )

    type_buttons = "\n".join(
        [
            f'<button class="filter-chip active" type="button" data-filter="all">All <span>{len(items)}</span></button>',
            f'<button class="filter-chip" type="button" data-filter="markdown">Markdown <span>{type_counts["markdown"]}</span></button>',
            f'<button class="filter-chip" type="button" data-filter="manual">Manual guides <span>{type_counts["manual"]}</span></button>',
            f'<button class="filter-chip" type="button" data-filter="html">Existing HTML <span>{type_counts["html"]}</span></button>',
        ]
    )

    chapter_shortcuts = "\n".join(
        f'<a href="#{key}"><strong>{html.escape(title.split(". ", 1)[-1])}</strong><span>{len(chapter_map[key][2])}</span></a>'
        for key, title, _desc in cfg_chapters()
        if chapter_map[key][2]
    )
    chapter_cards = "\n".join(
        chapter_card_html(index, key, title, desc, len(chapter_map[key][2]))
        for index, (key, title, desc) in enumerate(
            [
                (key, title, desc)
                for key, title, desc in cfg_chapters()
                if chapter_map[key][2]
            ],
            start=1,
        )
    )
    search_index_json = json.dumps(
        [search_index_entry(item) for item in items],
        ensure_ascii=False,
        separators=(",", ":"),
    ).replace("</", "<\\/")

    generated_at = generated_stamp()
    portal_href = PORTAL.relative_to(ROOT).as_posix()
    updated_pill = (
        f'<span class="pill">Updated: {html.escape(generated_at)}</span>'
        if generated_at
        else ""
    )
    cfg = CONFIG
    favicon_link = (
        f'\n  <link rel="icon" href="{cfg.favicon_href}">' if cfg.favicon_href else ""
    )
    theme_css = render_theme_css(cfg)
    brand_logo = (
        f'<img class="brand-logo" src="{cfg.logo_data_uri}" alt="">'
        if cfg.logo_data_uri
        else ""
    )
    footer_html = (
        f'  <footer class="site-footer">{html.escape(cfg.footer, quote=False)}</footer>\n'
        if cfg.footer
        else ""
    )
    html_page = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(cfg.name, quote=False)}</title>{favicon_link}
  <style>{common_css()}{theme_css}</style>
</head>
<body class="portal-page">
  <a class="skip-link" href="#main-content">Skip to content</a>
  <!-- {GENERATED_MARKER}: portal generated={generated_at} -->
  <header class="topbar">
    <div class="topbar-inner">
      <a class="brand" href="{html.escape(portal_href)}">{brand_logo}{html.escape(cfg.name, quote=False)}</a>
      <div class="top-search">
        <input class="search" id="doc-search" type="search" placeholder="Search documentation..." aria-label="Search documentation content">
      </div>
      <div class="top-actions">
        {updated_pill}
        <span class="pill">{md_count} Markdown</span>
        <span class="pill">{len(items)} link HTML</span>
      </div>
    </div>
  </header>

  <section class="hero">
    <div class="hero-copy">
      <p class="pill info">{html.escape(cfg.tagline, quote=False)}</p>
      <h1>{html.escape(cfg.name, quote=False)}</h1>
      <p>{html.escape(cfg.description, quote=False)}</p>
      <div class="hero-actions">
        <a class="button" href="#catalog">Open catalog</a>
        <a class="button secondary" href="#areas">Browse by area</a>
      </div>
    </div>
    <div class="hero-summary" aria-label="Documentation summary">
      <span class="hero-label">Local index</span>
      <strong>{len(items)}</strong>
      <p>HTML documents reachable from the dashboard.</p>
      <div class="hero-mini-grid">
        <span><b>{type_counts["markdown"]}</b>Markdown</span>
        <span><b>{type_counts["manual"]}</b>Manual</span>
        <span><b>{type_counts["html"]}</b>HTML</span>
      </div>
    </div>
  </section>

  <main class="dashboard-layout" id="main-content" tabindex="-1">
    <aside class="dashboard-sidebar">
      <h2>Navigation</h2>
      <nav>{nav}</nav>
      <h2 style="margin-top:18px;">Areas</h2>
      <nav class="chapter-shortcuts">{chapter_shortcuts}</nav>
    </aside>
    <div class="dashboard-content">
      <section class="section-panel" id="areas">
        <div class="section-panel-header">
          <div>
            <h2>Documentation Areas</h2>
            <p>A quick map of chapters with available document counts.</p>
          </div>
        </div>
        <div class="area-grid">
          {chapter_cards}
        </div>
      </section>

      <section class="section-panel" id="start">
        <div class="section-panel-header">
          <div>
            <h2>Quick Access</h2>
            <p>The documents you are most likely to need during daily work.</p>
          </div>
          <span class="pill info">{len(quick_cards)} shortcuts</span>
        </div>
        <div class="quick-grid">
          {''.join(quick_cards)}
        </div>
      </section>

      <section class="section-panel" id="catalog">
        <div class="section-panel-header">
          <div>
            <h2>Full Catalog</h2>
            <p>Filter guides, manual documents and existing HTML.</p>
          </div>
        </div>
        <div class="control-panel">
          <div class="control-row">
            <div class="filter-bar" aria-label="Filter documents">
              {type_buttons}
            </div>
          </div>
          <p class="result-count" id="result-count">{len(items)} visible documents.</p>
        </div>
      </section>

      <div class="catalog" id="doc-list">
        {''.join(sections)}
        <div class="empty" id="empty-state"><strong>No documents found.</strong> Try another search term or change the filter.</div>
      </div>
    </div>
  </main>

  <script>
    const searchIndex = {search_index_json};
    const searchMap = Object.fromEntries(searchIndex.map(function (item) {{
      return [item.id, item];
    }}));
    const searchInput = document.getElementById('doc-search');
    const cards = Array.from(document.querySelectorAll('.doc-card'));
    const chapters = Array.from(document.querySelectorAll('.chapter'));
    const emptyState = document.getElementById('empty-state');
    const resultCount = document.getElementById('result-count');
    const filterButtons = Array.from(document.querySelectorAll('.filter-chip'));
    let activeFilter = 'all';

    function termsFor(query) {{
      return query
        .split(/\\s+/)
        .map(function (term) {{ return term.trim().toLowerCase(); }})
        .filter(function (term) {{ return term.length > 1; }});
    }}

    function escapeRegExp(value) {{
      return value.replace(/[.*+?^${{}}()|[\\]\\\\]/g, '\\\\$&');
    }}

    function highlightSnippet(snippet, terms) {{
      let safe = snippet
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');

      terms.slice(0, 4).forEach(function (term) {{
        const pattern = new RegExp('(' + escapeRegExp(term) + ')', 'ig');
        safe = safe.replace(pattern, '<mark>$1</mark>');
      }});

      return safe;
    }}

    function makeSnippet(entry, terms) {{
      const preview = entry.preview || entry.summary || entry.title;
      const lower = preview.toLowerCase();
      let index = -1;

      terms.forEach(function (term) {{
        const found = lower.indexOf(term);
        if (found >= 0 && (index === -1 || found < index)) {{
          index = found;
        }}
      }});

      if (index < 0) {{
        return entry.summary;
      }}

      const start = Math.max(0, index - 90);
      const end = Math.min(preview.length, index + 210);
      const prefix = start > 0 ? '...' : '';
      const suffix = end < preview.length ? '...' : '';
      return prefix + preview.slice(start, end).trim() + suffix;
    }}

    function updateHit(card, entry, terms, showHit) {{
      const hit = card.querySelector('.doc-hit');
      if (!hit) return;

      if (!showHit || !terms.length) {{
        hit.hidden = true;
        hit.textContent = '';
        return;
      }}

      hit.innerHTML = highlightSnippet(makeSnippet(entry, terms), terms);
      hit.hidden = false;
    }}

    function applyFilters() {{
      const query = searchInput.value.trim().toLowerCase();
      const terms = termsFor(query);
      const hasQuery = terms.length > 0;
      let visibleCards = 0;

      cards.forEach(function (card) {{
        const entry = searchMap[card.dataset.docId] || {{
          searchText: card.dataset.search || '',
          summary: '',
          preview: ''
        }};
        const matchesQuery = !hasQuery || terms.every(function (term) {{
          return entry.searchText.includes(term);
        }});
        const matchesType = activeFilter === 'all' || card.dataset.type === activeFilter;
        const isVisible = matchesQuery && matchesType;
        card.style.display = isVisible ? '' : 'none';
        updateHit(card, entry, terms, hasQuery && isVisible);
        if (isVisible) visibleCards += 1;
      }});

      chapters.forEach(function (chapter) {{
        const hasVisibleCard = Array.from(chapter.querySelectorAll('.doc-card')).some(function (card) {{
          return card.style.display !== 'none';
        }});
        chapter.style.display = hasVisibleCard ? '' : 'none';
        if ((hasQuery || activeFilter !== 'all') && hasVisibleCard) {{
          chapter.open = true;
        }}
      }});

      emptyState.style.display = visibleCards ? 'none' : 'block';
      resultCount.textContent = visibleCards + (visibleCards === 1 ? ' visible document.' : ' visible documents.') + (hasQuery ? ' Full-content search is active.' : '');
    }}

    searchInput.addEventListener('input', applyFilters);
    filterButtons.forEach(function (button) {{
      button.addEventListener('click', function () {{
        filterButtons.forEach(function (item) {{
          item.classList.remove('active');
        }});
        button.classList.add('active');
        activeFilter = button.dataset.filter;
        applyFilters();
      }});
    }});
  </script>
{footer_html}</body>
</html>
"""
    return _write_if_changed(PORTAL, html_page)


def markdown_inline(text: str) -> str:
    value = re.sub(r"\s+", " ", text).strip()
    return value.replace("[", "\\[").replace("]", "\\]")


def markdown_link_path(path: Path) -> str:
    return quote(path.as_posix(), safe="/#.")


def llms_doc_line(item: DocItem) -> str:
    title = markdown_inline(item.title)
    link = markdown_link_path(item.link)
    summary = markdown_inline(item_summary(item))
    return f"- [{title}]({link}): {summary}"


def chapter_doc_sections(items: list[DocItem]) -> list[tuple[str, str, list[DocItem]]]:
    chapter_map: dict[str, list] = {
        key: [title, desc, []] for key, title, desc in cfg_chapters()
    }
    for item in sorted(items, key=lambda d: (d.chapter, d.path.as_posix().lower())):
        chapter_key = item.chapter
        if chapter_key not in chapter_map:
            chapter_map.setdefault(
                "other",
                ["Other", "Documents that were not classified automatically.", []],
            )
            chapter_key = "other"
        chapter_map[chapter_key][2].append(item)

    return [
        (title, desc, docs)
        for title, desc, docs in chapter_map.values()
        if docs
    ]


def render_llms_context(items: list[DocItem]) -> str:
    cfg = CONFIG
    generated_at = generated_stamp()
    timestamp = f" Generated: {generated_at}." if generated_at else ""
    portal_link = markdown_link_path(PORTAL.relative_to(ROOT))

    sections: list[str] = []
    for title, desc, docs in chapter_doc_sections(items):
        lines = "\n".join(llms_doc_line(item) for item in docs)
        sections.append(
            f"## {markdown_inline(title)}\n\n"
            f"{markdown_inline(desc)}\n\n"
            f"{lines}"
        )

    documents = "\n\n".join(sections) if sections else "No documents were found."
    description = markdown_inline(cfg.description)
    entry_points = [
        f"- [Searchable HTML portal]({portal_link}): "
        "Browse and search the full documentation portal."
    ]
    if (ROOT / "docs-portal.toml").exists():
        entry_points.append(
            "- [docs-portal.toml](docs-portal.toml): Project-specific portal configuration."
        )
    entry_points_text = "\n".join(entry_points)

    return (
        f"# {markdown_inline(cfg.name)}\n\n"
        f"> {description}\n\n"
        f"<!-- {GENERATED_MARKER}: llms -->\n\n"
        "This file is an LLM-friendly index generated by docs-portal."
        f"{timestamp}\n\n"
        "## Entry Points\n\n"
        f"{entry_points_text}\n\n"
        f"{documents}\n"
    )


def markdown_fence(content: str) -> str:
    longest = max(
        (len(match.group(0)) for match in re.finditer(r"`+", content)),
        default=0,
    )
    return "`" * max(3, longest + 1)


def llms_full_doc_section(item: DocItem) -> str:
    source = item.source.as_posix() if item.source else ""
    source_line = f"- Source: `{source}`\n" if source else ""
    content = item_content_text(item).strip() or "(No content extracted.)"
    fence = markdown_fence(content)
    return (
        f"### {markdown_inline(item.title)}\n\n"
        f"- Path: `{item.path.as_posix()}`\n"
        f"{source_line}"
        f"- Kind: `{item.kind}`\n"
        f"- Summary: {markdown_inline(item_summary(item))}\n\n"
        "#### Content\n\n"
        f"{fence}text\n"
        f"{content}\n"
        f"{fence}"
    )


def render_llms_full_context(items: list[DocItem]) -> str:
    cfg = CONFIG
    generated_at = generated_stamp()
    timestamp = f" Generated: {generated_at}." if generated_at else ""

    sections: list[str] = []
    for title, desc, docs in chapter_doc_sections(items):
        body = "\n\n".join(llms_full_doc_section(item) for item in docs)
        sections.append(
            f"## {markdown_inline(title)}\n\n"
            f"{markdown_inline(desc)}\n\n"
            f"{body}"
        )

    documents = "\n\n".join(sections) if sections else "No documents were found."
    description = markdown_inline(cfg.description)

    return (
        f"# {markdown_inline(cfg.name)} Full Context\n\n"
        f"> {description}\n\n"
        f"<!-- {GENERATED_MARKER}: llms-full -->\n\n"
        "This file contains the full text context extracted by docs-portal for "
        "LLMs, AI agents and RAG pipelines."
        f"{timestamp}\n\n"
        f"{documents}\n"
    )


def can_write_generated_context(path: Path) -> bool:
    if not path.exists():
        return True
    try:
        return GENERATED_MARKER in read_text(path)
    except OSError:
        return False


def write_llms_context(items: list[DocItem]) -> str:
    if not can_write_generated_context(LLMS):
        return "skipped"
    return _write_if_changed(LLMS, render_llms_context(items))


def write_llms_full_context(items: list[DocItem]) -> str:
    if not can_write_generated_context(LLMS_FULL):
        return "skipped"
    return _write_if_changed(LLMS_FULL, render_llms_full_context(items))


def parse_args(argv: list[str], default_root: Path) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="docs-portal",
        description="Build a static HTML documentation portal from a Markdown folder.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_root(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--root",
            type=Path,
            default=default_root,
            help="Root directory to scan (default: current directory).",
        )

    build = sub.add_parser("build", help="Generate HTML output (default command).")
    add_root(build)
    build.add_argument(
        "--out",
        default="DOCUMENTATION.html",
        help="Portal filename generated in the root (default: DOCUMENTATION.html).",
    )
    build.add_argument(
        "--config",
        type=Path,
        default=None,
        help="TOML config file (default: docs-portal.toml in the root).",
    )
    build.add_argument(
        "--no-timestamp",
        action="store_true",
        help="Omit the build timestamp for reproducible output and clean Git diffs.",
    )
    build.add_argument("--quiet", action="store_true", help="Suppress the final build summary.")

    init = sub.add_parser(
        "init", help="Create docs-portal.toml by detecting chapters from folders."
    )
    add_root(init)
    init.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path of the file to create (default: docs-portal.toml in the root).",
    )
    init.add_argument(
        "--force", action="store_true", help="Overwrite an existing docs-portal.toml."
    )

    return parser.parse_args(argv)


def cmd_build(args: argparse.Namespace) -> None:
    global ROOT, PORTAL, LLMS, LLMS_FULL, INCLUDE_TIMESTAMP, CONFIG

    ROOT = args.root.resolve()
    PORTAL = ROOT / args.out
    LLMS = ROOT / "llms.txt"
    LLMS_FULL = ROOT / "llms-full.txt"
    INCLUDE_TIMESTAMP = not args.no_timestamp

    config_path = args.config if args.config else (ROOT / "docs-portal.toml")
    CONFIG = load_site_config(config_path)

    md_files = walk_files(".md")
    markdown_docs, md_stats = build_markdown_docs(md_files)
    manual_checked, manual_updated = standardize_manual_doc_ui()
    generated_links = {item.link for item in markdown_docs}
    original_html = collect_original_html(generated_links)
    all_docs = markdown_docs + original_html
    portal_status = write_portal(all_docs, len(md_files))
    llms_status = write_llms_context(all_docs)
    llms_full_status = write_llms_full_context(all_docs)

    if not args.quiet:
        config_label = (
            str(config_path) if config_path.exists() else "default (no docs-portal.toml)"
        )
        portal_word = {"created": "created", "updated": "updated", "unchanged": "unchanged"}[
            portal_status
        ]
        touched = md_stats["created"] + md_stats["updated"]
        print("Build summary")
        print(f"  Root:           {ROOT}")
        print(f"  Config:         {config_label}")
        print(
            f"  Markdown ({len(md_files)}):   "
            f"{md_stats['created']} created · {md_stats['updated']} updated · "
            f"{md_stats['unchanged']} unchanged · {md_stats['skipped']} skipped"
        )
        print(
            f"  Manual docs:    {manual_checked} checked · {manual_updated} updated · "
            f"{manual_checked - manual_updated} unchanged"
        )
        print(f"  Existing HTML:  {len(original_html)} linked")
        print(f"  Portal:         {PORTAL.relative_to(ROOT).as_posix()} ({portal_word})")
        if llms_status == "skipped":
            print(
                "  LLM context:    llms.txt "
                "(skipped; existing file has no generated marker)"
            )
        else:
            print(f"  LLM context:    llms.txt ({llms_status})")
        if llms_full_status == "skipped":
            print(
                "  Full context:   llms-full.txt "
                "(skipped; existing file has no generated marker)"
            )
        else:
            print(f"  Full context:   llms-full.txt ({llms_full_status})")
        if md_stats["skipped"]:
            print(
                f"  Note: {md_stats['skipped']} .md files skipped "
                f"(HTML already exists without marker '{GENERATED_MARKER}')."
            )
        if (
            touched == 0
            and portal_status == "unchanged"
            and llms_status in {"unchanged", "skipped"}
            and llms_full_status in {"unchanged", "skipped"}
        ):
            print("  No changes: everything is already up to date.")


def detect_taxonomy(root: Path) -> tuple[list[tuple[str, str, str]], list[dict]]:
    """Infer chapters and rules from top-level folders that contain documents."""
    chapters: list[tuple[str, str, str]] = []
    rules: list[dict] = []
    used: set[str] = set()
    idx = 1

    root_docs = sorted(
        p.name
        for p in root.iterdir()
        if p.is_file()
        and p.suffix.lower() in {".md", ".html"}
        and p.name != "DOCUMENTATION.html"
    )
    if root_docs:
        chapters.append(("overview", f"{idx:02d}. Overview", "Documents in the project root."))
        used.add("overview")
        rules.append({"chapter": "overview", "name_in": [n.lower() for n in root_docs]})
        idx += 1

    for child in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if not child.is_dir() or child.name in EXCLUDED_DIRS or child.name.startswith("."):
            continue
        has_docs = next(child.rglob("*.md"), None) is not None or next(
            child.rglob("*.html"), None
        ) is not None
        if not has_docs:
            continue
        key = slugify(child.name, used)
        chapters.append(
            (key, f"{idx:02d}. {prettify_filename(child.name)}", f"Documents in the {child.name}/ folder.")
        )
        rules.append({"chapter": key, "startswith": [child.name.lower()]})
        idx += 1

    return chapters, rules


def render_init_toml(chapters: list[tuple[str, str, str]], rules: list[dict]) -> str:
    def q(value: str) -> str:
        return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'

    def arr(values: list) -> str:
        return "[" + ", ".join(q(v) for v in values) + "]"

    lines = [
        "# docs-portal configuration generated by `docs-portal init`.",
        "# Chapters and rules were inferred from existing folders; adjust as needed.",
        "",
        "[branding]",
        '# name = "My Documentation"',
        '# tagline = "Knowledge base"',
        '# logo = "assets/logo.png"',
        '# favicon = "\U0001f4da"',
        "",
        "# [colors]",
        '# blue = "#2563eb"',
        "",
    ]
    for key, title, desc in chapters:
        lines += ["[[chapters]]", f"key = {q(key)}", f"title = {q(title)}", f"description = {q(desc)}", ""]
    for rule in rules:
        lines += ["[[rules]]", f"chapter = {q(rule['chapter'])}"]
        for field_name in ("startswith", "contains", "name_in"):
            if field_name in rule:
                lines.append(f"{field_name} = {arr(rule[field_name])}")
        if "kind" in rule:
            lines.append(f"kind = {q(rule['kind'])}")
        lines.append("")
    return "\n".join(lines) + "\n"


def cmd_init(args: argparse.Namespace) -> None:
    global ROOT

    ROOT = args.root.resolve()
    config_path = args.config if args.config else (ROOT / "docs-portal.toml")
    if config_path.exists() and not args.force:
        print(f"{config_path} already exists. Use --force to overwrite it.")
        return

    chapters, rules = detect_taxonomy(ROOT)
    config_path.write_text(render_init_toml(chapters, rules), encoding="utf-8")

    print(f"Created {config_path}")
    print(f"Detected chapters: {len(chapters)}")
    for _key, title, _desc in chapters:
        print(f"  - {title}")
    print("Now build the documentation with:  docs-portal")


KNOWN_COMMANDS = {"build", "init"}


def main(argv: list[str] | None = None, default_root: Path | None = None) -> None:
    raw = list(sys.argv[1:] if argv is None else argv)
    # Senza sottocomando (o con sole opzioni) il default e' "build".
    if not raw or (raw[0] not in KNOWN_COMMANDS and raw[0] not in ("-h", "--help")):
        raw = ["build", *raw]
    if default_root is None:
        default_root = Path.cwd()

    args = parse_args(raw, default_root)
    if args.command == "init":
        cmd_init(args)
    else:
        cmd_build(args)


if __name__ == "__main__":
    main()
