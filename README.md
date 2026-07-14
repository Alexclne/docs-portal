# docs-portal

Turn a folder of Markdown into a **self-contained, searchable HTML documentation
portal** — with zero build configuration to start.

`docs-portal` converts every `.md` file into a styled HTML page, indexes any
hand-written `.html` you already have, and builds a single navigable portal with
client-side full-text search. Output pages are **self-contained** (CSS and images
inlined), so they work offline and can be copied or emailed as standalone files.

```bash
pipx install "git+https://github.com/Alexclne/docs-portal.git"
cd my-docs
docs-portal init               # auto-detects chapters from your folders
docs-portal                    # generates the HTML portal
```

## Features

- **Robust Markdown** — CommonMark via [markdown-it-py](https://github.com/executablebooks/markdown-it-py):
  nested lists, GFM tables, task lists, images, and automatic heading anchors + table of contents.
- **Mixed content** — indexes your existing hand-written `.html` alongside generated Markdown.
- **Self-contained output** — one HTML file per page, styles and assets inlined; no external requests.
- **LLM-friendly context** — generates `llms.txt` and `llms-full.txt` so AI
  tools can discover the portal structure or ingest the full cleaned content.
- **Documentation graph** — generates `docs-graph.html` and `docs-graph.json`
  to visualize internal links, orphan documents, hubs, and broken references.
- **Zero-config start, full control when needed** — sensible defaults, or a single
  `docs-portal.toml` for branding, colors, logo, and taxonomy.
- **Safe & idempotent** — only ever overwrites files it generated (marker-based);
  unchanged files are left untouched for clean diffs.
- **Reproducible builds** — `--no-timestamp` yields byte-identical output across runs.

## Requirements

- Python **3.11+**
- Dependencies (installed automatically): `markdown-it-py`, `mdit-py-plugins`

## Installation

Until `docs-portal` is published on PyPI, install it directly from GitHub.

```bash
# Isolated install (recommended)
pipx install "git+https://github.com/Alexclne/docs-portal.git"

# Into a virtual environment
python -m pip install "git+https://github.com/Alexclne/docs-portal.git"
```

To upgrade an existing `pipx` install:

```bash
pipx upgrade docs-portal
```

## Quick start

```bash
cd path/to/your/docs

docs-portal init        # writes docs-portal.toml (chapters detected from folders)
docs-portal             # equivalent to `docs-portal build`
open DOCUMENTATION.html  # macOS; use `xdg-open` on Linux
```

To serve it locally:

```bash
python -m http.server        # then open http://localhost:8000/DOCUMENTATION.html
```

> The default portal filename is `DOCUMENTATION.html`. Use `--out index.html`
> if you plan to host it as a website.

## Configuration

Everything is optional. Without a `docs-portal.toml`, built-in defaults are used.
A minimal example:

```toml
[branding]
name    = "Acme Documentation"
tagline = "Product knowledge base"
logo    = "assets/logo.png"   # embedded as a data URI (stays offline)
favicon = "📚"

[colors]
blue = "#2563eb"              # primary accent

[[chapters]]
key = "guides"
title = "01. Guides"
description = "How-to guides and tutorials."

[[rules]]
chapter = "guides"
startswith = ["guides/"]
```

See **[docs/configuration.md](docs/configuration.md)** for the full reference.

## Documentation

- [Getting started](docs/getting-started.md) — install and build your first portal.
- [Configuration reference](docs/configuration.md) — every `docs-portal.toml` option.
- [CLI reference](docs/cli.md) — commands and flags.
- [Contributing](CONTRIBUTING.md) — development setup and tests.
- [Changelog](CHANGELOG.md).

## How it works

`docs-portal` scans the target directory (default: current directory) for `.md`
and `.html` files, skipping hidden directories and common noise
(`node_modules`, `.git`, `dist`, `build`, `__pycache__`, …).

- Each `.md` becomes a `.html` page marked with an HTML comment
  (`ts-docs-generated`). **Only files carrying that marker are ever overwritten**,
  so your hand-written HTML is safe.
- Existing `.html` files are indexed into the portal as-is.
- `llms.txt` and `llms-full.txt` are generated with the same marker-based safety
  model. Hand-written files without the marker are left untouched.
- `docs-graph.html` and `docs-graph.json` are generated from internal `.md` and
  `.html` links using the same marker-based safety model.
- Relative links to `.md` files are rewritten to point at the generated `.html`.

## License

[MIT](LICENSE)
