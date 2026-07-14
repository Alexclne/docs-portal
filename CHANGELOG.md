# Changelog

All notable changes to this project are documented in this file. The format is
based on [Keep a Changelog](https://keepachangelog.com/), and this project adheres
to [Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-07-13

### Added

- Initial release.
- `build` command: convert Markdown into self-contained HTML pages and build a
  searchable portal index.
- `init` command: auto-detect chapters from top-level folders and write a
  starter `docs-portal.toml`.
- CommonMark rendering via markdown-it-py — nested lists, GFM tables, task lists,
  images, heading anchors, per-page table of contents, and relative `.md`→`.html`
  link rewriting.
- Indexing of existing hand-written HTML alongside generated Markdown.
- Configurable branding, colors, logo (base64-embedded), favicon, footer, and
  full taxonomy (chapters, classification rules, quick links, summaries) via
  `docs-portal.toml`.
- Idempotent writes (only changed files are rewritten) and `--no-timestamp` for
  reproducible builds.
- LLM-friendly `llms.txt` and `llms-full.txt` generation for documentation
  portals.
- Build report summarizing created / updated / unchanged / skipped documents.
- pytest test suite.
