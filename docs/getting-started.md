# Getting started

This guide takes you from an empty install to a working documentation portal in
a couple of minutes.

## 1. Install

`docs-portal` needs **Python 3.11+**. Until it is published on PyPI, install it
directly from GitHub:

```bash
# Isolated install (recommended)
pipx install "git+https://github.com/Alexclne/docs-portal.git"

# Into the active virtual environment
python -m pip install "git+https://github.com/Alexclne/docs-portal.git"
```

Its dependencies (`markdown-it-py`, `mdit-py-plugins`) are installed automatically.

## 2. Point it at a folder of Markdown

Any folder works. For example:

```
my-docs/
├── README.md
├── guides/
│   ├── installation.md
│   └── usage.md
└── reference/
    └── api.md
```

## 3. Generate a starter config (optional but recommended)

```bash
cd my-docs
docs-portal init
```

`init` inspects your top-level folders and writes a `docs-portal.toml` with one
chapter per folder (files in the root become an "Overview" chapter). Open the
file to rename chapters, set your brand name, colors, or a logo — see the
[configuration reference](configuration.md).

Running `init` again refuses to overwrite an existing config unless you pass
`--force`.

## 4. Build the portal

```bash
docs-portal            # same as: docs-portal build
```

You will get:

- one generated `*.html` next to each `*.md`
- a portal index (`DOCUMENTATION.html` by default) with search and navigation
- `llms.txt` and `llms-full.txt` context files for LLMs and AI agents

Open it:

```bash
open DOCUMENTATION.html           # macOS
xdg-open DOCUMENTATION.html       # Linux
```

Or serve it:

```bash
python -m http.server            # http://localhost:8000/DOCUMENTATION.html
```

## 5. Iterate

Add or edit Markdown, then re-run `docs-portal`. The build report tells you
exactly what changed:

```
Build summary
  Markdown (12):   1 created · 2 updated · 9 unchanged · 0 skipped
  LLM context:    llms.txt (updated)
  Full context:   llms-full.txt (updated)
  ...
```

Only files whose content actually changed are rewritten, so re-building is cheap
and keeps version-control diffs clean. Add `--no-timestamp` for byte-identical,
reproducible output.

## Next steps

- Customize branding, colors, and taxonomy: [configuration reference](configuration.md).
- All commands and flags: [CLI reference](cli.md).
