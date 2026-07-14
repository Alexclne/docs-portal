# Repository Instructions for GitHub Copilot

This repository contains `docs-portal`, a Python CLI for generating
self-contained, searchable HTML documentation portals from Markdown folders.

When working in this repository:

- Keep user-facing strings, docs and examples in English.
- Prefer focused, minimal changes that match the current single-package Python
  layout.
- Put behavior changes in `src/docs_portal/__init__.py` unless a clearer local
  module boundary already exists.
- Keep generated output safe and idempotent. Do not overwrite hand-written files
  unless they contain the appropriate generated marker.
- Treat `llms.txt` and `llms-full.txt` as generated outputs unless they are
  hand-written files without the generated marker.
- Preserve deterministic builds with `--no-timestamp`.
- Update README/docs/examples when CLI behavior, defaults or public config
  surface changes.
- Add or update pytest coverage for behavior changes.

Useful commands:

```bash
python -m pip install -e ".[test]"
pytest
docs-portal --help
```

Key files:

- `src/docs_portal/__init__.py`: CLI, rendering, config, build and init logic.
- `src/docs_portal/docs_portal.css`: embedded portal stylesheet.
- `docs/`: user documentation.
- `examples/`: configuration examples.
- `tests/`: pytest suite.
