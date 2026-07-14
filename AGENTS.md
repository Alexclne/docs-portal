# Agent Instructions

`docs-portal` is a Python CLI that turns Markdown folders into self-contained,
searchable HTML documentation portals.

## Project Map

- `src/docs_portal/__init__.py`: main implementation, including CLI parsing,
  Markdown rendering, configuration loading, portal generation and init logic.
- `src/docs_portal/__main__.py`: enables `python -m docs_portal`.
- `src/docs_portal/docs_portal.css`: packaged stylesheet embedded into generated
  HTML output.
- `docs/`: user-facing documentation.
- `examples/`: sample `docs-portal.toml` files.
- `tests/`: pytest suite.
- `.github/workflows/ci.yml`: GitHub Actions test workflow.

## Development Commands

```bash
python -m pip install -e ".[test]"
pytest
docs-portal --help
```

Use `python3 -m venv .venv` first when working outside an existing virtual
environment.

## Project Rules

- Keep user-facing text in English.
- Keep generated HTML self-contained: inline CSS and local assets; no external
  runtime requests.
- Do not overwrite hand-written HTML unless it contains the generated marker
  `ts-docs-generated`.
- Keep writes idempotent. Use `_write_if_changed` for generated files so
  unchanged output does not churn Git diffs.
- Preserve reproducible output when `--no-timestamp` is used.
- Add or update focused tests for behavior changes.
- Update the relevant documentation when CLI behavior, defaults or config keys
  change.

## Implementation Notes

- Markdown rendering uses `markdown-it-py` plus `mdit-py-plugins`.
- The default portal filename is `DOCUMENTATION.html`.
- Existing hand-written HTML is indexed into the portal but left untouched unless
  explicitly listed in `manual_docs`.
- Classification is driven by `docs-portal.toml` rules when present, otherwise
  the built-in chapter defaults are used.
