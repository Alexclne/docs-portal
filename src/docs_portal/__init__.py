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
GRAPH_JSON = ROOT / "docs-graph.json"
GRAPH_HTML = ROOT / "docs-graph.html"
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


def document_source_path(item: DocItem) -> Path:
    if item.source is not None:
        return ROOT / item.source
    return ROOT / item.path


def clean_link_label(value: str, fallback: str) -> str:
    label = strip_html_text(value)
    label = strip_inline(label)
    label = re.sub(r"\s+", " ", label).strip()
    return label or fallback


def markdown_doc_links(text: str) -> list[tuple[str, str]]:
    tokens = _markdown_parser().parse(text, {})
    links: list[tuple[str, str]] = []

    for token in tokens:
        if token.type != "inline" or not token.children:
            continue
        children = token.children
        index = 0
        while index < len(children):
            child = children[index]
            if child.type != "link_open":
                index += 1
                continue

            href = child.attrGet("href") or ""
            label_parts: list[str] = []
            index += 1
            while index < len(children) and children[index].type != "link_close":
                if getattr(children[index], "content", ""):
                    label_parts.append(children[index].content)
                index += 1

            links.append((href, clean_link_label(" ".join(label_parts), href)))
            index += 1

    return links


def html_doc_links(text: str) -> list[tuple[str, str]]:
    links: list[tuple[str, str]] = []
    pattern = re.compile(
        r"<a\b[^>]*\bhref=(['\"])(.*?)\1[^>]*>(.*?)</a>",
        flags=re.I | re.S,
    )
    for _quote, href, label in pattern.findall(text):
        links.append((href, clean_link_label(label, href)))
    return links


def item_doc_links(item: DocItem) -> list[tuple[str, str]]:
    source_path = document_source_path(item)
    if not source_path.exists():
        return []

    text = read_text(source_path)
    if source_path.suffix.lower() == ".md":
        return markdown_doc_links(text)
    if source_path.suffix.lower() == ".html":
        return html_doc_links(text)
    return []


def strip_url_fragment_and_query(href: str) -> str:
    value = href.strip()
    if "#" in value:
        value = value.split("#", 1)[0]
    if "?" in value:
        value = value.split("?", 1)[0]
    return value


def graph_target_path(source_file: Path, href: str) -> Path | None:
    value = strip_url_fragment_and_query(href)
    if not value:
        return None
    if re.match(r"^(https?:|mailto:|tel:|data:|javascript:)", value, flags=re.I):
        return None
    if value.startswith("<") and value.endswith(">"):
        value = value[1:-1]

    target = (source_file.parent / value).resolve()
    if not target.is_relative_to(ROOT):
        return None

    rel_target = target.relative_to(ROOT)
    suffix = rel_target.suffix.lower()
    if suffix == ".md":
        rel_target = rel_target.with_suffix(".html")
    elif suffix != ".html":
        return None

    return rel_target


def graph_node_positions(nodes: list[dict]) -> None:
    chapter_order = {
        key: index for index, (key, _title, _desc) in enumerate(cfg_chapters())
    }
    grouped: dict[str, list[dict]] = {}
    for node in sorted(
        nodes,
        key=lambda item: (chapter_order.get(item["chapterKey"], 999), item["id"]),
    ):
        grouped.setdefault(node["chapterKey"], []).append(node)

    width = 1200
    height = 720
    margin_x = 110
    margin_y = 92
    groups = list(grouped.values())
    if not groups:
        return

    for group_index, group in enumerate(groups):
        if len(groups) == 1:
            x = width / 2
        else:
            x = margin_x + group_index * ((width - (margin_x * 2)) / (len(groups) - 1))
        for item_index, node in enumerate(group):
            y = margin_y + (item_index + 1) * (
                (height - (margin_y * 2)) / (len(group) + 1)
            )
            node["x"] = round(x, 2)
            node["y"] = round(y, 2)


def build_docs_graph(items: list[DocItem]) -> dict:
    sorted_items = sorted(items, key=lambda item: item.path.as_posix().lower())
    doc_by_id = {item.path.as_posix().lower(): item for item in sorted_items}
    nodes: list[dict] = []
    edges: list[dict] = []
    broken_links: list[dict] = []
    edge_seen: set[tuple[str, str, str]] = set()
    incoming: dict[str, int] = {}
    outgoing: dict[str, int] = {}

    for item in sorted_items:
        item_id = item.path.as_posix()
        chapter_title, _chapter_desc = chapter_meta(item.chapter)
        nodes.append(
            {
                "id": item_id,
                "title": item.title,
                "chapterKey": item.chapter,
                "chapter": chapter_title,
                "kind": item.kind,
                "type": doc_type(item),
                "summary": item_summary(item),
                "url": item.link.as_posix(),
                "source": item.source.as_posix() if item.source else "",
            }
        )
        incoming[item_id] = 0
        outgoing[item_id] = 0

    for item in sorted_items:
        source_id = item.path.as_posix()
        source_path = document_source_path(item)
        for href, label in item_doc_links(item):
            target_path = graph_target_path(source_path, href)
            if target_path is None:
                continue

            target_id = target_path.as_posix()
            if target_id == source_id:
                continue

            target = doc_by_id.get(target_id.lower())
            if target is None:
                broken_links.append(
                    {
                        "source": source_id,
                        "target": target_id,
                        "label": label,
                        "href": href,
                    }
                )
                continue

            key = (source_id, target.path.as_posix(), label)
            if key in edge_seen:
                continue
            edge_seen.add(key)
            edges.append(
                {"source": source_id, "target": target.path.as_posix(), "label": label}
            )
            outgoing[source_id] += 1
            incoming[target.path.as_posix()] += 1

    for node in nodes:
        node["incoming"] = incoming[node["id"]]
        node["outgoing"] = outgoing[node["id"]]

    graph_node_positions(nodes)
    orphans = [node["id"] for node in nodes if node["incoming"] == 0]
    hubs = sorted(nodes, key=lambda node: (-node["outgoing"], node["title"].lower()))[:10]

    return {
        "marker": GENERATED_MARKER,
        "generated": generated_stamp(),
        "portal": PORTAL.relative_to(ROOT).as_posix(),
        "stats": {
            "nodes": len(nodes),
            "edges": len(edges),
            "brokenLinks": len(broken_links),
            "orphans": len(orphans),
        },
        "nodes": nodes,
        "edges": edges,
        "brokenLinks": broken_links,
        "orphans": orphans,
        "hubs": [
            {"id": node["id"], "title": node["title"], "outgoing": node["outgoing"]}
            for node in hubs
            if node["outgoing"] > 0
        ],
    }


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
    graph_href = GRAPH_HTML.relative_to(ROOT).as_posix()
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
        <a class="button secondary" href="{html.escape(graph_href)}">View graph</a>
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
    graph_link = markdown_link_path(GRAPH_HTML.relative_to(ROOT))

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
        "Browse and search the full documentation portal.",
        f"- [Documentation graph]({graph_link}): "
        "Visualize internal links, orphan documents and broken references.",
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


def render_docs_graph_json(graph: dict) -> str:
    return json.dumps(graph, ensure_ascii=False, indent=2) + "\n"


def render_docs_graph_html(graph: dict) -> str:
    cfg = CONFIG
    graph_json = json.dumps(graph, ensure_ascii=False, separators=(",", ":")).replace(
        "</", "<\\/"
    )
    generated_at = generated_stamp()
    generated_badge = (
        f'<span class="pill">Updated: {html.escape(generated_at)}</span>'
        if generated_at
        else ""
    )
    portal_href = PORTAL.relative_to(ROOT).as_posix()
    favicon_link = (
        f'\n  <link rel="icon" href="{html.escape(cfg.favicon_href, quote=True)}">'
        if cfg.favicon_href
        else ""
    )
    theme_css = render_theme_css(cfg)
    page = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>__GRAPH_TITLE__</title>__FAVICON_LINK__
  <style>
__COMMON_CSS__
__THEME_CSS__
    body.graph-page {
      background: var(--bg);
      color: var(--ink);
    }
    .graph-shell {
      max-width: 1440px;
      min-height: 100vh;
      margin: 0 auto;
      padding: 20px;
    }
    .graph-header {
      border: 1px solid var(--line);
      background: var(--panel);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
      margin-bottom: 20px;
      padding: 18px;
    }
    .graph-header-main {
      align-items: center;
      display: flex;
      gap: 18px;
      justify-content: space-between;
    }
    .eyebrow {
      color: var(--blue);
      font-size: 12px;
      font-weight: 800;
      letter-spacing: .07em;
      margin: 0 0 6px;
      text-transform: uppercase;
    }
    .title-block h1 {
      color: var(--ink);
      font-size: 30px;
      line-height: 1.15;
      margin: 0;
    }
    .title-block p {
      color: var(--muted);
      line-height: 1.5;
      margin: 8px 0 0;
      max-width: 780px;
    }
    .header-actions { align-items: center; display: flex; flex-wrap: wrap; gap: 10px; }
    .graph-layout {
      display: grid;
      gap: 20px;
      grid-template-columns: 300px minmax(0, 1fr) 360px;
      width: 100%;
    }
    .graph-page .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
      overflow: hidden;
    }
    .control-panel, .side-panel { padding: 16px; }
    .control-panel h2, .side-panel h2 {
      border: 0;
      color: #64748b;
      font-size: 13px;
      letter-spacing: .07em;
      margin: 0 0 12px;
      padding: 0;
      text-transform: uppercase;
    }
    .control-panel h3, .side-panel h3 {
      border-top: 1px solid var(--line);
      color: #64748b;
      font-size: 12px;
      letter-spacing: .06em;
      margin: 16px 0 10px;
      padding-top: 14px;
      text-transform: uppercase;
    }
    .search-box {
      background: #fff;
      border: 1px solid var(--line);
      border-radius: 6px;
      color: var(--ink);
      font: inherit;
      min-height: 40px;
      outline: none;
      padding: 10px 12px;
      width: 100%;
    }
    .search-box:focus {
      border-color: var(--blue);
      box-shadow: 0 0 0 3px rgba(29, 78, 216, .14);
    }
    .metric-grid {
      display: grid;
      gap: 8px;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      margin-top: 12px;
    }
    .metric {
      background: var(--gray-light);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      padding: 10px;
    }
    .metric strong { color: var(--ink); display: block; font-size: 22px; line-height: 1; }
    .metric span { color: var(--muted); display: block; font-size: 12px; margin-top: 5px; }
    .filter-grid, .legend { display: grid; gap: 8px; }
    .filter-button {
      align-items: center;
      background: #fff;
      border: 1px solid var(--line);
      border-radius: 6px;
      color: var(--ink);
      cursor: pointer;
      display: flex;
      justify-content: space-between;
      min-height: 40px;
      padding: 8px 10px;
      text-align: left;
      width: 100%;
    }
    .filter-button:hover {
      border-color: var(--blue-mid);
      background: var(--blue-bg);
    }
    .filter-button.active {
      background: var(--blue-bg);
      border-color: var(--blue);
      color: var(--blue);
    }
    .legend-dot { border-radius: 50%; display: inline-block; height: 10px; margin-right: 8px; width: 10px; }
    .graph-stage { min-height: calc(100vh - 164px); position: relative; }
    .stage-toolbar {
      align-items: center;
      display: flex;
      gap: 10px;
      justify-content: space-between;
      padding: 14px 16px;
      position: relative;
      z-index: 2;
    }
    .stage-title strong { display: block; font-size: 15px; }
    .stage-title span { color: var(--muted); display: block; font-size: 12px; margin-top: 2px; }
    .stage-actions { display: flex; flex-wrap: wrap; gap: 8px; }
    .ghost-button {
      background: #fff;
      border: 1px solid var(--line);
      border-radius: 6px;
      color: var(--ink);
      cursor: pointer;
      font-weight: 700;
      min-height: 34px;
      padding: 7px 10px;
    }
    .ghost-button:hover {
      border-color: var(--blue);
      color: var(--blue);
      background: var(--blue-bg);
    }
    .graph-canvas {
      background: #fff;
      border-top: 1px solid var(--line);
      height: calc(100% - 64px);
      min-height: 620px;
      overflow: hidden;
      position: relative;
    }
    .graph-canvas::before {
      background-image:
        linear-gradient(#eef2f7 1px, transparent 1px),
        linear-gradient(90deg, #eef2f7 1px, transparent 1px);
      background-size: 42px 42px;
      content: "";
      inset: 0;
      opacity: .82;
      pointer-events: none;
      position: absolute;
    }
    svg { display: block; height: 100%; min-height: 620px; position: relative; width: 100%; z-index: 1; }
    .edge {
      fill: none;
      opacity: .72;
      stroke: #94a3b8;
      stroke-width: 1.6;
    }
    .edge.neighborhood { opacity: .95; stroke: var(--blue); stroke-width: 2.4; }
    .node { cursor: pointer; }
    .node circle {
      cursor: pointer;
      stroke: #fff;
      stroke-width: 1.5;
    }
    .node text {
      fill: var(--ink);
      font-size: 12px;
      font-weight: 800;
      paint-order: stroke;
      pointer-events: none;
      stroke: #fff;
      stroke-linejoin: round;
      stroke-width: 4px;
    }
    .node .node-type { fill: var(--muted); font-size: 10px; font-weight: 700; }
    .node.selected circle { stroke: var(--blue); stroke-width: 4; }
    .node.dim, .edge.dim { opacity: .13; }
    .node.hidden, .edge.hidden { display: none; }
    .status-strip {
      bottom: 12px;
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      left: 16px;
      position: absolute;
      z-index: 3;
    }
    .doc-title { font-size: 19px; font-weight: 800; margin: 0 0 8px; }
    .doc-meta, .doc-summary { color: var(--muted); line-height: 1.5; margin: 0 0 10px; }
    .connection-grid { display: grid; gap: 8px; }
    .connection-card {
      background: var(--gray-light);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      padding: 9px 10px;
    }
    .connection-card strong { display: block; font-size: 13px; }
    .connection-card span { color: var(--muted); display: block; font-size: 12px; margin-top: 3px; }
    .list { display: grid; gap: 8px; margin: 0; padding: 0; }
    .list li {
      background: var(--gray-light);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      list-style: none;
      padding: 9px 10px;
    }
    .list small { color: var(--muted); display: block; margin-top: 2px; }
    .graph-page .empty {
      color: var(--muted);
      display: block;
      font-size: 13px;
      padding: 0;
    }
    @media (max-width: 980px) {
      .graph-shell { padding: 12px; }
      .graph-header-main, .stage-toolbar { align-items: flex-start; flex-direction: column; }
      .graph-layout { grid-template-columns: 1fr; }
      .graph-stage { min-height: 640px; }
      .graph-canvas, svg { min-height: 560px; }
    }
  </style>
</head>
<body class="graph-page">
  <!-- __GENERATED_MARKER__: graph generated=__GENERATED_AT__ -->
  <div class="graph-shell">
    <header class="graph-header">
      <div class="graph-header-main">
        <div class="title-block">
          <p class="eyebrow">docs-portal knowledge graph</p>
          <h1>__GRAPH_HEADING__</h1>
          <p>Explore how documents connect, find hubs, surface orphan pages, and spot broken references without leaving the generated portal.</p>
        </div>
        <div class="header-actions">
          __GENERATED_BADGE__
          <a class="button secondary" href="__PORTAL_HREF__">Portal</a>
        </div>
      </div>
    </header>

    <main class="graph-layout">
      <aside class="panel control-panel">
        <h2>Explore</h2>
        <input class="search-box" id="graph-search" type="search" placeholder="Search documents..." aria-label="Search graph documents">
        <div class="metric-grid" id="graph-stats"></div>
        <h3>Focus</h3>
        <div class="filter-grid" id="graph-filters">
          <button class="filter-button active" type="button" data-mode="all">All documents <span></span></button>
          <button class="filter-button" type="button" data-mode="hubs">Hubs <span></span></button>
          <button class="filter-button" type="button" data-mode="orphans">Orphans <span></span></button>
          <button class="filter-button" type="button" data-mode="broken">Broken refs <span></span></button>
        </div>
        <h3>Communities</h3>
        <div class="legend" id="community-legend"></div>
      </aside>

      <section class="panel graph-stage">
        <div class="stage-toolbar">
          <div class="stage-title">
            <strong>Interactive graph</strong>
            <span>Drag nodes, search, or select a community to inspect the structure.</span>
          </div>
          <div class="stage-actions">
            <button class="ghost-button" type="button" id="reset-view">Reset view</button>
            <button class="ghost-button" type="button" id="toggle-labels">Labels on</button>
          </div>
        </div>
        <div class="graph-canvas">
          <svg id="graph-svg" viewBox="0 0 1200 720" role="img" aria-label="Documentation link graph"></svg>
          <div class="status-strip" id="graph-status"></div>
        </div>
      </section>

      <aside class="panel side-panel" aria-live="polite">
        <h2>Selected Document</h2>
        <div id="selected-doc" class="empty">Select a node to inspect it.</div>
        <h3>Connections</h3>
        <div id="selected-connections" class="connection-grid"></div>
        <h3>Broken Links</h3>
        <ul id="broken-links" class="list"></ul>
        <h3>Orphan Documents</h3>
        <ul id="orphan-docs" class="list"></ul>
      </aside>
    </main>
  </div>

  <script type="application/json" id="graph-data">__GRAPH_JSON__</script>
  <script>
    const graph = JSON.parse(document.getElementById('graph-data').textContent);
    const svg = document.getElementById('graph-svg');
    const search = document.getElementById('graph-search');
    const selectedDoc = document.getElementById('selected-doc');
    const selectedConnections = document.getElementById('selected-connections');
    const stats = document.getElementById('graph-stats');
    const statusStrip = document.getElementById('graph-status');
    const brokenLinks = document.getElementById('broken-links');
    const orphanDocs = document.getElementById('orphan-docs');
    const legend = document.getElementById('community-legend');
    const filters = Array.from(document.querySelectorAll('[data-mode]'));
    const resetView = document.getElementById('reset-view');
    const toggleLabels = document.getElementById('toggle-labels');
    const nodeById = new Map(graph.nodes.map(function (node) { return [node.id, node]; }));
    const nodeElements = new Map();
    const labelElements = new Map();
    const edgeElements = [];
    const palette = ['#1d4ed8', '#15803d', '#c2410c', '#64748b', '#b91c1c', '#0f766e', '#475569', '#92400e'];
    const chapters = Array.from(new Set(graph.nodes.map(function (node) { return node.chapter; })));
    const colorByChapter = new Map(chapters.map(function (chapter, index) {
      return [chapter, palette[index % palette.length]];
    }));
    const incomingByNode = new Map();
    const outgoingByNode = new Map();
    let activeMode = 'all';
    let labelsVisible = true;
    let selectedId = '';

    graph.nodes.forEach(function (node) {
      node.vx = 0;
      node.vy = 0;
      node.radius = 12 + Math.min(12, Math.sqrt((node.incoming || 0) + (node.outgoing || 0)) * 4);
      incomingByNode.set(node.id, []);
      outgoingByNode.set(node.id, []);
    });
    graph.edges.forEach(function (edge) {
      if (outgoingByNode.has(edge.source)) outgoingByNode.get(edge.source).push(edge);
      if (incomingByNode.has(edge.target)) incomingByNode.get(edge.target).push(edge);
    });

    function escapeHtml(value) {
      return String(value).replace(/[&<>"']/g, function (char) {
        return {
          '&': '&amp;',
          '<': '&lt;',
          '>': '&gt;',
          '"': '&quot;',
          "'": '&#39;'
        }[char];
      });
    }

    function svgEl(name, attrs) {
      const element = document.createElementNS('http://www.w3.org/2000/svg', name);
      Object.entries(attrs || {}).forEach(function (entry) {
        element.setAttribute(entry[0], entry[1]);
      });
      return element;
    }

    function metric(label, value) {
      return '<div class="metric"><strong>' + escapeHtml(value) + '</strong><span>' + escapeHtml(label) + '</span></div>';
    }

    function renderStats() {
      stats.innerHTML = [
        metric('Documents', graph.stats.nodes),
        metric('Links', graph.stats.edges),
        metric('Broken', graph.stats.brokenLinks),
        metric('Orphans', graph.stats.orphans)
      ].join('');
      statusStrip.innerHTML = [
        '<span class="pill">Nodes: ' + graph.stats.nodes + '</span>',
        '<span class="pill">Edges: ' + graph.stats.edges + '</span>',
        '<span class="pill">Communities: ' + chapters.length + '</span>'
      ].join('');
      const modeCounts = {
        all: graph.nodes.length,
        hubs: graph.nodes.filter(function (node) { return node.outgoing > 1 || node.incoming > 1; }).length,
        orphans: graph.orphans.length,
        broken: new Set(graph.brokenLinks.map(function (link) { return link.source; })).size
      };
      filters.forEach(function (button) {
        const span = button.querySelector('span');
        if (span) span.textContent = modeCounts[button.dataset.mode] || 0;
      });
    }

    function renderLegend() {
      legend.innerHTML = chapters.map(function (chapter) {
        return '<button class="filter-button" type="button" data-chapter="' + escapeHtml(chapter) + '">' +
          '<span><i class="legend-dot" style="background:' + colorByChapter.get(chapter) + '"></i>' + escapeHtml(chapter) + '</span>' +
          '<span>' + graph.nodes.filter(function (node) { return node.chapter === chapter; }).length + '</span>' +
          '</button>';
      }).join('');
      legend.querySelectorAll('[data-chapter]').forEach(function (button) {
        button.addEventListener('click', function () {
          search.value = button.dataset.chapter;
          activeMode = 'all';
          filters.forEach(function (item) { item.classList.toggle('active', item.dataset.mode === 'all'); });
          applyFilters();
        });
      });
    }

    function visibleByMode(node) {
      if (activeMode === 'hubs') return node.outgoing > 1 || node.incoming > 1;
      if (activeMode === 'orphans') return graph.orphans.includes(node.id);
      if (activeMode === 'broken') return graph.brokenLinks.some(function (link) { return link.source === node.id; });
      return true;
    }

    function neighborsOf(id) {
      const result = new Set([id]);
      (incomingByNode.get(id) || []).forEach(function (edge) { result.add(edge.source); });
      (outgoingByNode.get(id) || []).forEach(function (edge) { result.add(edge.target); });
      return result;
    }

    function clearSelection() {
      selectedId = '';
      nodeElements.forEach(function (element) {
        element.classList.remove('selected', 'dim');
      });
      edgeElements.forEach(function (edge) {
        edge.element.classList.remove('neighborhood', 'dim');
      });
      selectedDoc.textContent = 'Select a node to inspect it.';
      selectedDoc.className = 'empty';
      selectedConnections.innerHTML = '';
    }

    function selectNode(id) {
      selectedId = id;
      const neighborhood = neighborsOf(id);
      nodeElements.forEach(function (element, nodeId) {
        element.classList.toggle('selected', nodeId === id);
        element.classList.toggle('dim', selectedId && !neighborhood.has(nodeId));
      });
      edgeElements.forEach(function (edge) {
        const inNeighborhood = edge.source === id || edge.target === id;
        edge.element.classList.toggle('neighborhood', inNeighborhood);
        edge.element.classList.toggle('dim', selectedId && !inNeighborhood);
      });
      const node = nodeById.get(id);
      if (!node) {
        selectedDoc.textContent = 'Select a node to inspect it.';
        selectedDoc.className = 'empty';
        selectedConnections.innerHTML = '';
        return;
      }
      selectedDoc.className = '';
      selectedDoc.innerHTML =
        '<p class="doc-title">' + escapeHtml(node.title) + '</p>' +
        '<p class="doc-meta">' + escapeHtml(node.chapter) + ' · ' + escapeHtml(node.kind) + '</p>' +
        '<p class="doc-summary">' + escapeHtml(node.summary) + '</p>' +
        '<p class="doc-meta">Incoming: ' + node.incoming + ' · Outgoing: ' + node.outgoing + '</p>' +
        '<a class="button" href="' + escapeHtml(node.url) + '">Open document</a>';
      const rows = [
        ...(outgoingByNode.get(id) || []).map(function (edge) {
          const target = nodeById.get(edge.target);
          return '<div class="connection-card"><strong>→ ' + escapeHtml(target ? target.title : edge.target) + '</strong><span>' + escapeHtml(edge.label || 'links to') + '</span></div>';
        }),
        ...(incomingByNode.get(id) || []).map(function (edge) {
          const source = nodeById.get(edge.source);
          return '<div class="connection-card"><strong>← ' + escapeHtml(source ? source.title : edge.source) + '</strong><span>' + escapeHtml(edge.label || 'linked from') + '</span></div>';
        })
      ];
      selectedConnections.innerHTML = rows.length ? rows.join('') : '<p class="empty">No internal graph connections.</p>';
    }

    function renderGraph() {
      svg.innerHTML = '';
      const defs = svgEl('defs');
      const marker = svgEl('marker', {
        id: 'arrow',
        markerWidth: '10',
        markerHeight: '10',
        refX: '8',
        refY: '3',
        orient: 'auto',
        markerUnits: 'strokeWidth'
      });
      marker.appendChild(svgEl('path', { d: 'M0,0 L0,6 L9,3 z', fill: '#94a3b8' }));
      defs.appendChild(marker);
      svg.appendChild(defs);

      graph.edges.forEach(function (edge) {
        const source = nodeById.get(edge.source);
        const target = nodeById.get(edge.target);
        if (!source || !target) return;
        const path = svgEl('path', {
          class: 'edge',
          'marker-end': 'url(#arrow)'
        });
        svg.appendChild(path);
        edgeElements.push({ element: path, source: edge.source, target: edge.target });
      });

      graph.nodes.forEach(function (node) {
        const group = svgEl('g', { class: 'node', tabindex: '0' });
        group.dataset.nodeId = node.id;
        group.appendChild(svgEl('circle', { r: node.radius, fill: colorByChapter.get(node.chapter) || '#60a5fa' }));
        const label = svgEl('text', { x: node.radius + 8, y: -2 });
        label.textContent = node.title;
        group.appendChild(label);
        const type = svgEl('text', { class: 'node-type', x: node.radius + 8, y: 14 });
        type.textContent = node.chapter;
        group.appendChild(type);
        labelElements.set(node.id, [label, type]);
        group.addEventListener('click', function () { selectNode(node.id); });
        group.addEventListener('keydown', function (event) {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            selectNode(node.id);
          }
        });
        group.addEventListener('pointerdown', function (event) {
          node.dragging = true;
          group.setPointerCapture(event.pointerId);
        });
        group.addEventListener('pointermove', function (event) {
          if (!node.dragging) return;
          const point = svg.createSVGPoint();
          point.x = event.clientX;
          point.y = event.clientY;
          const cursor = point.matrixTransform(svg.getScreenCTM().inverse());
          node.x = cursor.x;
          node.y = cursor.y;
          node.vx = 0;
          node.vy = 0;
          tick();
        });
        group.addEventListener('pointerup', function (event) {
          node.dragging = false;
          group.releasePointerCapture(event.pointerId);
        });
        svg.appendChild(group);
        nodeElements.set(node.id, group);
      });
      settleGraph(140);
      clearSelection();
    }

    function tick() {
      graph.edges.forEach(function (edge, index) {
        const source = nodeById.get(edge.source);
        const target = nodeById.get(edge.target);
        const edgeView = edgeElements[index];
        if (!source || !target || !edgeView) return;
        const dx = target.x - source.x;
        const dy = target.y - source.y;
        const cx = (source.x + target.x) / 2 - dy * .08;
        const cy = (source.y + target.y) / 2 + dx * .08;
        edgeView.element.setAttribute('d', 'M' + source.x + ',' + source.y + ' Q' + cx + ',' + cy + ' ' + target.x + ',' + target.y);
      });
      graph.nodes.forEach(function (node) {
        const element = nodeElements.get(node.id);
        if (element) element.setAttribute('transform', 'translate(' + node.x + ' ' + node.y + ')');
      });
    }

    function settleGraph(iterations) {
      const width = 1200;
      const height = 720;
      for (let step = 0; step < iterations; step += 1) {
        for (let i = 0; i < graph.nodes.length; i += 1) {
          const a = graph.nodes[i];
          for (let j = i + 1; j < graph.nodes.length; j += 1) {
            const b = graph.nodes[j];
            let dx = b.x - a.x;
            let dy = b.y - a.y;
            let distance = Math.sqrt(dx * dx + dy * dy) || 1;
            const force = 2600 / (distance * distance);
            dx /= distance;
            dy /= distance;
            if (!a.dragging) { a.vx -= dx * force; a.vy -= dy * force; }
            if (!b.dragging) { b.vx += dx * force; b.vy += dy * force; }
          }
        }
        graph.edges.forEach(function (edge) {
          const source = nodeById.get(edge.source);
          const target = nodeById.get(edge.target);
          if (!source || !target) return;
          const dx = target.x - source.x;
          const dy = target.y - source.y;
          const distance = Math.sqrt(dx * dx + dy * dy) || 1;
          const pull = (distance - 180) * .006;
          const fx = (dx / distance) * pull;
          const fy = (dy / distance) * pull;
          if (!source.dragging) { source.vx += fx; source.vy += fy; }
          if (!target.dragging) { target.vx -= fx; target.vy -= fy; }
        });
        graph.nodes.forEach(function (node) {
          if (!node.dragging) {
            node.vx += (width / 2 - node.x) * .002;
            node.vy += (height / 2 - node.y) * .002;
            node.x = Math.max(42, Math.min(width - 42, node.x + node.vx));
            node.y = Math.max(42, Math.min(height - 42, node.y + node.vy));
            node.vx *= .82;
            node.vy *= .82;
          }
        });
      }
      tick();
    }

    function renderList(element, rows, emptyText, renderItem) {
      if (!rows.length) {
        element.innerHTML = '<li class="empty">' + emptyText + '</li>';
        return;
      }
      element.innerHTML = rows.map(renderItem).join('');
    }

    function renderSideLists() {
      renderList(
        brokenLinks,
        graph.brokenLinks,
        'No broken document links found.',
        function (link) {
          const source = nodeById.get(link.source);
          const sourceTitle = source ? source.title : link.source;
          return '<li><strong>' + escapeHtml(sourceTitle) + '</strong><small>' + escapeHtml(link.target) + '</small></li>';
        }
      );
      renderList(
        orphanDocs,
        graph.orphans,
        'No orphan documents found.',
        function (id) {
          const node = nodeById.get(id);
          const title = node ? node.title : id;
          return '<li><a href="#" data-node-id="' + escapeHtml(id) + '">' + escapeHtml(title) + '</a><small>' + escapeHtml(id) + '</small></li>';
        }
      );
      orphanDocs.querySelectorAll('[data-node-id]').forEach(function (link) {
        link.addEventListener('click', function (event) {
          event.preventDefault();
          selectNode(link.dataset.nodeId);
        });
      });
    }

    function applySearch() {
      const query = search.value.trim().toLowerCase();
      const visible = new Set();
      graph.nodes.forEach(function (node) {
        const haystack = [node.title, node.id, node.chapter, node.kind, node.summary].join(' ').toLowerCase();
        const match = visibleByMode(node) && (!query || haystack.includes(query));
        if (match) visible.add(node.id);
        const element = nodeElements.get(node.id);
        if (element) element.classList.toggle('hidden', !match);
      });
      edgeElements.forEach(function (edge) {
        edge.element.classList.toggle(
          'hidden',
          !visible.has(edge.source) || !visible.has(edge.target)
        );
      });
    }

    renderStats();
    renderLegend();
    renderGraph();
    renderSideLists();
    applySearch();
    search.addEventListener('input', applySearch);
    filters.forEach(function (button) {
      button.addEventListener('click', function () {
        activeMode = button.dataset.mode;
        filters.forEach(function (item) { item.classList.toggle('active', item === button); });
        applySearch();
      });
    });
    resetView.addEventListener('click', function () {
      settleGraph(220);
      applySearch();
      clearSelection();
    });
    toggleLabels.addEventListener('click', function () {
      labelsVisible = !labelsVisible;
      toggleLabels.textContent = labelsVisible ? 'Labels on' : 'Labels off';
      labelElements.forEach(function (labels) {
        labels.forEach(function (label) { label.style.display = labelsVisible ? '' : 'none'; });
      });
    });
  </script>
</body>
</html>
"""
    return (
        page.replace("__GRAPH_TITLE__", f"{html.escape(cfg.name, quote=False)} Documentation Graph")
        .replace("__GRAPH_HEADING__", f"{html.escape(cfg.name, quote=False)} Graph")
        .replace("__FAVICON_LINK__", favicon_link)
        .replace("__COMMON_CSS__", common_css())
        .replace("__THEME_CSS__", theme_css)
        .replace("__GENERATED_MARKER__", GENERATED_MARKER)
        .replace("__GENERATED_AT__", html.escape(generated_at))
        .replace("__GENERATED_BADGE__", generated_badge)
        .replace("__PORTAL_HREF__", html.escape(portal_href))
        .replace("__GRAPH_JSON__", graph_json)
    )


def write_docs_graph_json(graph: dict) -> str:
    if not can_write_generated_context(GRAPH_JSON):
        return "skipped"
    return _write_if_changed(GRAPH_JSON, render_docs_graph_json(graph))


def write_docs_graph_html(graph: dict) -> str:
    if not can_write_generated_context(GRAPH_HTML):
        return "skipped"
    return _write_if_changed(GRAPH_HTML, render_docs_graph_html(graph))


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
    global ROOT, PORTAL, LLMS, LLMS_FULL, GRAPH_JSON, GRAPH_HTML, INCLUDE_TIMESTAMP, CONFIG

    ROOT = args.root.resolve()
    PORTAL = ROOT / args.out
    LLMS = ROOT / "llms.txt"
    LLMS_FULL = ROOT / "llms-full.txt"
    GRAPH_JSON = ROOT / "docs-graph.json"
    GRAPH_HTML = ROOT / "docs-graph.html"
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
    graph = build_docs_graph(all_docs)
    graph_json_status = write_docs_graph_json(graph)
    graph_html_status = write_docs_graph_html(graph)

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
        if graph_json_status == "skipped" or graph_html_status == "skipped":
            print(
                "  Graph:          docs-graph.html / docs-graph.json "
                "(skipped where existing files have no generated marker)"
            )
        else:
            print(
                "  Graph:          "
                f"docs-graph.html ({graph_html_status}), "
                f"docs-graph.json ({graph_json_status})"
            )
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
            and graph_json_status in {"unchanged", "skipped"}
            and graph_html_status in {"unchanged", "skipped"}
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
