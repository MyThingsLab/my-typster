# my-typster — agent instructions

You are developing **my-typster**, a MyThingsLab My[X] tool.

**Inherited rules:** obey [`./HARNESS.md`](./HARNESS.md) in full — the vendored
MyThingsLab build-harness rules. Do not restate or override them. Anything not
covered here defers to `HARNESS.md`, then `my-things-core/docs/CONVENTIONS.md`.

## This tool

- **Purpose:** given a document-drafting issue labeled `my-typster` (a report,
  note, article, resume, or letter, plus a `kind` telling it which template to
  use), drafts the content and typesets it as Typst source, compiles it to a
  PDF with the real `typst` CLI, and opens a PR carrying both the `.typ`
  source and the compiled PDF.
- **The single Engine call:** one per run — "given this content request and an
  existing Typst style anchor (a `templates/<kind>.typ` file from the shared
  `typst-templates` repo), draft the Typst source." Input: the issue title +
  body, the anchor's full text, `context = {"issue": N, "kind": str,
  "anchor_path": str}`. Output: `{"typ_source": str}`. The model may only use
  `#import`s already present in the anchor — a structural fence enforced by
  the writer, not an Engine-trusted claim. Against `NoopEngine` (or an
  unparsable/over-scoped reply), degrades to the anchor's header (everything
  up to its `// === body ===` marker) unchanged, with the issue body inserted
  verbatim as the body — honest stub, never fabricated prose.
- **Invariants / rules:**
  - **Compile gate.** The Engine call produces source, not a validated
    document. After the Engine call, run `typst compile` (the real CLI,
    never the model) in the isolated `Workspace`. If it fails, **no PR** —
    post the compiler's error as an issue comment instead, `outcome
    =compile_failed`. Only a successful compile carries both `.typ` and
    `.pdf` into the PR.
  - **Personal-kind fence.** `resume` and `letter` are personal-document
    kinds. Drafting one against any repo other than the private
    `MyThingsLab/typst-personal-docs` is refused outright, before the issue
    body is even read for content — every other MyThingsLab repo is public
    by convention, and personal content must never reach one. `outcome
    =failure`, no Engine call.
  - Templates are read from a **separate, shared** `typst-templates` repo
    (a local checkout, `--templates-repo`, default `../typst-templates`),
    not from the target repo's own tree — unlike MySite's in-repo anchors,
    Typst templates are reused across every target repo.
  - One side effect: a **committed PR via `Workspace`**, routed through
    `Policy` (`Guard` default). **Never merges.**
  - `mytypster draft` supports `--json` to print a machine-readable result —
    this is MyPresentation's hand-off point (it shells out to this CLI for
    `kind=presentation` rather than importing this package).
  - Ledger `kind=typst_doc`, `outcome=success|compile_failed|failure`.
- **Backlog label:** `my-typster`
