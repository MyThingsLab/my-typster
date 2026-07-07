# my-typster

[![CI](https://github.com/MyThingsLab/my-typster/actions/workflows/ci.yml/badge.svg)](https://github.com/MyThingsLab/my-typster/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/MyThingsLab/my-typster/branch/main/graph/badge.svg)](https://codecov.io/gh/MyThingsLab/my-typster)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)
![MIT](https://img.shields.io/badge/license-MIT-green)

A [MyThingsLab](../mythings-core) `My[X]` tool: given a document-drafting
issue (label `my-typster`, a `kind` of report/note/letter/resume/...), drafts
Typst source against a style anchor, compiles it with the real `typst` CLI,
and opens a PR carrying both the `.typ` source and the compiled PDF.

MyTypster owns *typesetting* only — turning a content request into idiomatic,
compiling Typst. It doesn't own document narrative structure — see
[MyPresentation](../my-presentation) for talks.

## Usage

```bash
# Draft issue #12 on a target repo, open a PR.
mytypster draft --issue 12 --repo owner/target --engine claude-cli

# Explicit kind, skip opening a PR:
mytypster draft --issue 12 --repo owner/target --kind report --no-pr
```

Templates live in a separate, shared checkout (`--templates-repo`, default
`../typst-templates`) — reused across every target repo, unlike per-repo
content anchors. Each invocation makes **at most one** Engine call. Against
the default `--engine noop`, drafting degrades to the anchor's header
(imports/settings) unchanged with the issue body inserted verbatim as the
body — an honest degrade, never fabricated prose.

## Compile gate

The Engine call produces *source*, not a validated document. After drafting,
the real `typst compile` runs in an isolated worktree. If it fails, **no PR
opens** — the compiler's error is posted as an issue comment instead
(`outcome=compile_failed`). Only a successful compile carries both the
`.typ` and `.pdf` into the PR.

## Structural fence

The Engine may only use `#import` lines already present in the style
anchor — a new package dependency is never added on the model's own
judgment. An over-scoped reply degrades to the header/body stub rather than
failing the run.

## Personal-document fence

`resume` and `letter` are personal-document kinds. Every other MyThingsLab
repo is public by convention, so drafting one against any repo other than
the private `MyThingsLab/typst-personal-docs` is refused outright, before
the issue body is even read — `outcome=failure`, no Engine call, no PR.

## MyPresentation hand-off

`mytypster draft --from-json <slides.json> --kind presentation` skips the
Engine call and deterministically renders a slide-data payload
(`{"slides": [{"title", "bullets"}]}`) against the `presentation` anchor.
This is MyPresentation's integration point — it shells out to this CLI
rather than importing this package, keeping the two tools decoupled at the
code level. Pass `--json` to get a machine-readable result on stdout.

## Install (development)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ../mythings-core -e ../my-guard -e ".[dev]"
pytest
```

Compiling for real (not just running the mocked test suite) needs the
`typst` CLI on `PATH` — see [`typst.app`](https://typst.app) or the version
pinned in `.github/workflows/ci.yml`.

See [`CLAUDE.md`](CLAUDE.md) for the tool's seams and [`HARNESS.md`](HARNESS.md)
for the inherited build rules.

## License

MIT — see [`LICENSE`](LICENSE).
