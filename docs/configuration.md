# Configuration reference

`docs-portal` reads an optional **`docs-portal.toml`** from the root directory
(or a path given with `--config`). Every section and key is optional: anything
you omit falls back to a built-in default, so a build works with no config at all.

Generate a starting point automatically with [`docs-portal init`](cli.md#init).

## `[branding]`

Text and assets shown across the portal and the generated pages.

| Key | Type | Description |
| --- | --- | --- |
| `name` | string | Site name — used in the portal title, header, and page `<title>` suffix. |
| `tagline` | string | Short subtitle shown in the portal hero. |
| `description` | string | Longer descriptive paragraph in the portal hero. |
| `doc_title_suffix` | string | Suffix appended to each page's `<title>`. Defaults to `name` when omitted. |
| `logo` | path | Image embedded as a base64 data URI (keeps pages offline). Resolved relative to the config file. |
| `favicon` | string | An emoji (rendered as an SVG icon) **or** a path to an image. |
| `footer` | string | Footer text for the portal. Omit for no footer. |

```toml
[branding]
name    = "Acme Documentation"
tagline = "Product knowledge base"
logo    = "assets/logo.png"
favicon = "📚"
footer  = "© 2026 Acme Inc."
```

## `[colors]`

Overrides for the theme's CSS custom properties. Keys are variable names without
the `--` prefix; values are any valid CSS color. They are emitted as a `:root`
block appended after the base stylesheet.

Commonly overridden: `blue` (primary accent), `bg` (page background),
`dark` (sidebar/header), `ink` (text), `muted` (secondary text), `line`
(borders), plus `green`, `orange`, `red`.

```toml
[colors]
blue = "#2563eb"
dark = "#111827"
bg   = "#f7f5f2"
```

## `[[chapters]]`

The chapters shown in the portal, in the order listed. Each document is placed
into a chapter by the classification rules below; the `key` links the two.

| Key | Description |
| --- | --- |
| `key` | Stable identifier, also used as the section anchor. |
| `title` | Display title. |
| `description` | Short description shown under the title. |

```toml
[[chapters]]
key = "guides"
title = "01. Guides"
description = "How-to guides and tutorials."
```

Documents that match no rule fall into a built-in **`other`** chapter.

## `[[rules]]`

Classification rules, evaluated top to bottom — **first match wins**. A document
is assigned to `chapter` if **any** of the conditions matches:

| Key | Matches when… |
| --- | --- |
| `startswith` | the file path (lowercased, relative to root) starts with any listed prefix |
| `contains` | the file path contains any listed substring |
| `name_in` | the file name equals any listed name |
| `kind` | set to `"html"` to match hand-written HTML files |

```toml
[[rules]]
chapter = "guides"
startswith = ["guides/"]
contains = ["howto"]

[[rules]]
chapter = "reference"
startswith = ["reference/", "api/"]

[[rules]]
chapter = "overview"
name_in = ["readme.md", "index.md"]
```

## `[[quick_links]]`

Shortcut cards shown in the portal's "quick access" area. A card is rendered only
if a document with the given `path` exists.

| Key | Description |
| --- | --- |
| `path` | Path (relative to root) of the target HTML page. |
| `label` | Card title. |
| `description` | Card text. |
| `area` | Small tag/badge on the card. |

```toml
[[quick_links]]
path = "guides/installation.html"
label = "Installation"
description = "Get up and running in five minutes."
area = "Guides"
```

## `[[summaries]]`

Per-document summary text shown on its card. Rules are evaluated in order —
**first match wins** — otherwise the document's chapter description is used.

Path/title conditions are combined with **OR**; `kind`, if present, with **AND**:

| Key | Matches against |
| --- | --- |
| `contains` | substrings in the file path |
| `endswith` | suffixes of the file path |
| `title_contains` | substrings in the document title |
| `kind` | the document kind (e.g. `"HTML originale"` for hand-written HTML) |
| `text` | **required** — the summary to display |

```toml
[[summaries]]
contains = ["installation"]
text = "Step-by-step installation instructions."

[[summaries]]
title_contains = ["changelog"]
text = "Release history and notable changes."
```

## `manual_docs`

A list of hand-written HTML files whose `<style>` block should be replaced with
the shared theme, so they match the look of generated pages. Only these files are
touched; all other hand-written HTML is left exactly as-is.

```toml
manual_docs = [
  "handbook/onboarding.html",
  "handbook/security.html",
]
```

## `open_chapters`

Chapter keys that should be expanded by default in the portal.

```toml
open_chapters = ["overview", "guides"]
```

## A note on defaults

The package ships with a set of defaults tailored to its original project. When
you run `docs-portal init`, the generated `docs-portal.toml` **replaces** those
defaults with chapters and rules derived from *your* folders — which is why you
should run `init` (or write a config) when using the tool on a new project.
