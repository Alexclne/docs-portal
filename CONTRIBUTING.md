# Contributing

Thanks for your interest in improving `docs-portal`.

## Development setup

Requires Python 3.11+.

```bash
git clone https://github.com/USER/docs-portal.git
cd docs-portal
python -m venv .venv
source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -e ".[test]"
```

## Running the tests

```bash
pytest
```

The suite covers Markdown rendering, document classification, configuration
loading, the auto-detection used by `init`, and idempotent writes.

## Project layout

```
docs-portal/
├── pyproject.toml                 # metadata, dependencies, `docs-portal` entry point
├── src/docs_portal/
│   ├── __init__.py                # all logic: rendering, portal, config, CLI, init
│   ├── __main__.py                # enables `python -m docs_portal`
│   └── docs_portal.css            # shared stylesheet (packaged as data)
├── tests/                         # pytest suite
├── examples/                      # sample docs-portal.toml files
└── docs/                          # this documentation
```

Markdown rendering is delegated to [markdown-it-py](https://github.com/executablebooks/markdown-it-py);
the module handles the portal, the taxonomy/config, and the CLI.

## Guidelines

- **Keep it dependency-light.** New runtime dependencies should be well justified.
- **Add tests** for new behavior or bug fixes.
- **Deterministic output.** Generated HTML must stay byte-identical across runs
  for the same input (respect `--no-timestamp`); avoid embedding non-reproducible
  data. There are tests that rely on this.
- **Write only what changed.** File writes go through the idempotent helper so
  unchanged files are not rewritten — keep it that way.
- Match the style of the surrounding code.

## Reporting issues

When filing a bug, please include:

- your Python version and OS,
- the command you ran,
- a minimal folder of Markdown that reproduces the problem (if possible),
- what you expected versus what happened.
