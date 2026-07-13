from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from myguard import Guard
from mythings.engine import Engine, EngineRequest, NoopEngine
from mythings.github import GitHub, GitHubError, PullRequest, Runner, _gh, _pr_number
from mythings.isolation import Workspace, in_github_actions
from mythings.ledger import Ledger
from mythings.policy import Action, Decision, Policy

LABEL = "my-typster"
BODY_MARKER = "// === body ==="

# Personal-document kinds must never land in a public repo. Routing them
# anywhere else is refused outright, before the issue body is even read for
# content — a structural fence, not a trust-the-caller convention.
PERSONAL_KINDS = frozenset({"resume", "letter"})
PERSONAL_REPO = "MyThingsLab/typst-personal-docs"

_KIND_KEYWORDS = {
    "report": {"report"},
    "note": {"note", "notes"},
    "letter": {"letter", "letters"},
    "resume": {"resume", "cv"},
}

_SYSTEM = (
    "You draft Typst source for a document, matching the style anchor's "
    "existing settings. You may only use #import lines already present in "
    "the anchor — never add a new package dependency. Reply with a single "
    "JSON object {\"typ_source\": str} and nothing else."
)


class PolicyDenied(RuntimeError):
    pass


@dataclass(frozen=True)
class Result:
    outcome: str  # success | compile_failed | failure
    kind: str | None
    pr: int | None
    detail: str
    typ_path: str | None = None
    pdf_path: str | None = None

    def to_json(self) -> dict[str, object]:
        return {
            "outcome": self.outcome,
            "kind": self.kind,
            "pr": self.pr,
            "detail": self.detail,
            "typ_path": self.typ_path,
            "pdf_path": self.pdf_path,
        }


@dataclass(frozen=True)
class _Issue:
    number: int
    title: str
    body: str
    labels: list[str]


def _tokenize(text: str) -> list[str]:
    out: list[str] = []
    word: list[str] = []
    for ch in text.lower():
        if ch.isalnum():
            word.append(ch)
        elif word:
            out.append("".join(word))
            word = []
    if word:
        out.append("".join(word))
    return out


def _slug(text: str) -> str:
    out = []
    for ch in text.lower().strip():
        if ch.isalnum():
            out.append(ch)
        elif out and out[-1] != "-":
            out.append("-")
    return "".join(out).strip("-")[:60] or "doc"


def _infer_kind(issue: _Issue, available: set[str]) -> str:
    for label in issue.labels:
        if label in available:
            return label
    tokens = set(_tokenize(issue.title) + _tokenize(issue.body))
    for kind, keywords in _KIND_KEYWORDS.items():
        if kind in available and tokens & keywords:
            return kind
    return "default"


_IMPORT_RE = re.compile(r'^\s*#import\s+"([^"]+)"', re.MULTILINE)


def _imports(text: str) -> set[str]:
    return set(_IMPORT_RE.findall(text))


def _split_header(anchor_text: str) -> tuple[str, str]:
    idx = anchor_text.find(BODY_MARKER)
    if idx == -1:
        return anchor_text, ""
    end_of_line = anchor_text.find("\n", idx)
    header_end = end_of_line + 1 if end_of_line != -1 else len(anchor_text)
    return anchor_text[:header_end], anchor_text[header_end:]


def _stub_source(anchor_text: str, topic: _Issue) -> str:
    header, _ = _split_header(anchor_text)
    body = topic.body.strip() or f"Draft placeholder for {topic.title}."
    return f"{header}= {topic.title}\n{body}\n"


class Typster:
    def __init__(
        self,
        *,
        repo_root: str | Path = ".",
        repo: str | None = None,
        templates_repo: str | Path = "../typst-templates",
        ledger: Ledger,
        base: str = "main",
        engine: Engine | None = None,
        policy: Policy | None = None,
        runner: Runner = _gh,
        typst_runner: Runner | None = None,
    ) -> None:
        self.repo_root = Path(repo_root)
        self.repo = repo
        self.templates_repo = Path(templates_repo)
        self.ledger = ledger
        self.base = base
        self.engine: Engine = engine or NoopEngine()
        self.policy: Policy = policy or Guard()
        self.runner = runner
        self.github = GitHub(repo, runner=runner)
        self._typst_run = typst_runner or _default_typst_runner

    # ---- draft ----------------------------------------------------------

    def draft(
        self,
        issue: int,
        *,
        kind: str | None = None,
        no_pr: bool = False,
        from_slides: dict[str, object] | None = None,
    ) -> Result:
        try:
            topic = self._fetch_issue(issue)
        except GitHubError as err:
            return self._fail(None, f"could not read issue #{issue}: {err}")

        available = self._available_kinds()
        resolved_kind = kind or _infer_kind(topic, available)

        if resolved_kind in PERSONAL_KINDS and self.repo != PERSONAL_REPO:
            detail = (
                f"personal document kind '{resolved_kind}' must be requested in "
                f"the private {PERSONAL_REPO} repo, not {self.repo}"
            )
            return self._fail(resolved_kind, detail)

        anchor_path, anchor_text = self._anchor(resolved_kind, available)

        if from_slides is not None:
            typ_source = _render_presentation(anchor_text, from_slides)
        else:
            reply = self.engine.run(
                EngineRequest(
                    system=_SYSTEM,
                    prompt=self._prompt(topic, resolved_kind, anchor_path, anchor_text),
                    context={"issue": issue, "kind": resolved_kind, "anchor_path": anchor_path},
                )
            )
            typ_source = self._parse_reply(reply.text, anchor_text, topic)

        slug = _slug(topic.title)
        typ_path = f"{slug}.typ"

        with Workspace(self.repo_root, self.base) as tree:
            (tree / typ_path).write_text(typ_source, encoding="utf-8")
            ok, pdf_path, compiler_error = self._compile(tree, typ_path)

            if not ok:
                self._comment(issue, f"Typst compile failed:\n\n```\n{compiler_error}\n```")
                return self._record_and_return(
                    "compile_failed",
                    topic.number,
                    resolved_kind,
                    None,
                    f"compile failed for {slug}",
                    typ_path,
                )

            pr = None
            if not no_pr:
                try:
                    pr = self._open_pr(tree, topic, slug, resolved_kind, typ_path, pdf_path)
                except PolicyDenied as denied:
                    return self._fail(resolved_kind, str(denied))

        detail = f"draft for {slug} ({resolved_kind})"
        return self._record_and_return(
            "success",
            topic.number,
            resolved_kind,
            pr.number if pr else None,
            detail,
            typ_path,
            pdf_path,
        )

    # ---- deterministic pre-work ------------------------------------------

    def _fetch_issue(self, number: int) -> _Issue:
        argv = ["issue", "view", str(number), "--json", "number,title,body,labels"]
        if self.repo:
            argv += ["--repo", self.repo]
        obj = json.loads(self.runner(argv))
        labels = [lbl["name"] if isinstance(lbl, dict) else lbl for lbl in obj.get("labels", [])]
        return _Issue(
            number=obj["number"], title=obj["title"], body=obj.get("body") or "", labels=labels
        )

    def _available_kinds(self) -> set[str]:
        directory = self.templates_repo / "templates"
        if not directory.is_dir():
            return set()
        return {p.stem for p in directory.glob("*.typ")}

    def _anchor(self, kind: str, available: set[str]) -> tuple[str, str]:
        name = kind if kind in available else "default"
        path = self.templates_repo / "templates" / f"{name}.typ"
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        return f"templates/{name}.typ", text

    def _prompt(self, topic: _Issue, kind: str, anchor_path: str, anchor_text: str) -> str:
        lines = [
            f"Issue #{topic.number}: {topic.title}",
            f"Kind: {kind}",
            f"\nRequest body:\n{topic.body.strip()}",
            f"\nStyle anchor ({anchor_path}):\n{anchor_text}",
            '\nReturn JSON with key "typ_source" (the full .typ file body).',
        ]
        return "\n".join(lines)

    # ---- engine reply parsing / structural fence -------------------------

    def _parse_reply(self, text: str, anchor_text: str, topic: _Issue) -> str:
        obj = _parse_json_object(text)
        if obj is None:
            return _stub_source(anchor_text, topic)

        typ_source = obj.get("typ_source")
        if not isinstance(typ_source, str) or not typ_source.strip():
            return _stub_source(anchor_text, topic)

        if not _imports(typ_source) <= _imports(anchor_text):
            return _stub_source(anchor_text, topic)  # over-scoped reply degrades safely

        return typ_source

    # ---- compile gate -----------------------------------------------------

    def _compile(self, tree: Path, typ_path: str) -> tuple[bool, str | None, str]:
        proc = self._typst_run(["compile", str(tree / typ_path)])
        pdf_path = typ_path[: -len(".typ")] + ".pdf" if typ_path.endswith(".typ") else typ_path
        ok = (tree / pdf_path).exists()
        return ok, (pdf_path if ok else None), proc

    # ---- github / git helpers ---------------------------------------------

    def _open_pr(
        self,
        tree: Path,
        topic: _Issue,
        slug: str,
        kind: str,
        typ_path: str,
        pdf_path: str | None,
    ) -> PullRequest:
        branch = f"{LABEL}/{topic.number}"
        existing = self._existing_pr(branch)
        self._git(tree, ["checkout", "-B", branch])
        self._git(tree, ["add", typ_path])
        if pdf_path:
            self._git(tree, ["add", pdf_path])
        self._git(tree, ["commit", "-m", f"typster: draft {slug} ({kind})"])
        self._git(tree, ["push", "-u" if existing is None else "", "origin", branch])
        if existing is not None:
            return existing
        self._guard(f"gh pr create --head {branch} --base {self.base}")
        return self.github.open_pr(
            title=f"typster: {topic.title}",
            body=f"Drafted `{slug}` ({kind}) as Typst source, compiled to PDF.\n\n"
            f"Closes #{topic.number}.",
            base=self.base,
            head=branch,
        )

    def _existing_pr(self, branch: str) -> PullRequest | None:
        argv = ["pr", "list", "--head", branch, "--state", "open", "--json", "number,url"]
        if self.repo:
            argv += ["--repo", self.repo]
        rows = json.loads(self.runner(argv))
        if not rows:
            return None
        row = rows[0]
        return PullRequest(number=row.get("number") or _pr_number(row["url"]), url=row["url"])

    def _comment(self, issue: int, body: str) -> str | None:
        if self.repo is None:
            return None
        argv = ["issue", "comment", str(issue), "--repo", self.repo, "--body", body]
        action = Action(kind="bash", payload={"command": f"gh issue comment {issue}"})
        if self.policy.evaluate(action).under(unattended=in_github_actions()) is not Decision.ALLOW:
            return None
        return self.runner(argv).strip() or None

    def _git(self, tree: Path, argv: list[str]) -> None:
        argv = [a for a in argv if a != ""]
        self._guard("git " + " ".join(argv))
        proc = subprocess.run(["git", "-C", str(tree), *argv], capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"git {' '.join(argv)} failed: {proc.stderr.strip()}")

    def _guard(self, command: str) -> None:
        action = Action(kind="fs-write", payload={"command": command})
        result = self.policy.evaluate(action)
        if result.under(unattended=in_github_actions()) is not Decision.ALLOW:
            raise PolicyDenied(f"policy blocked: {command} ({result.reason or result.decision})")

    # ---- ledger / results ---------------------------------------------------

    def _record_and_return(
        self,
        outcome: str,
        issue: int,
        kind: str | None,
        pr: int | None,
        detail: str,
        typ_path: str | None,
        pdf_path: str | None = None,
    ) -> Result:
        data: dict[str, object] = {"issue": issue, "doc_kind": kind, "typ_path": typ_path}
        if outcome == "success":
            data["pdf_path"] = pdf_path
            data["pr_url"] = pr
        self.ledger.record(
            tool="mytypster", kind="typst_doc", outcome=outcome, detail=detail, **data
        )
        return Result(outcome, kind, pr, detail, typ_path, pdf_path)

    def _fail(self, kind: str | None, detail: str) -> Result:
        self.ledger.record(
            tool="mytypster", kind="typst_doc", outcome="failure", detail=detail, doc_kind=kind
        )
        return Result("failure", kind, None, detail)


def _render_presentation(anchor_text: str, slides_payload: dict[str, object]) -> str:
    # Deterministic templating for MyPresentation's hand-off: no Engine call,
    # slide data -> Typst slide-package syntax using the presentation anchor.
    header, _ = _split_header(anchor_text)
    slides = slides_payload.get("slides") or []
    blocks = []
    for slide in slides:
        title = str(slide.get("title", ""))
        bullets = slide.get("bullets") or []
        notes = str(slide.get("speaker_notes", "") or "")
        body_lines = "\n".join(f"- {_escape_markup(str(b))}" for b in bullets) or "_(no content)_"
        notes_arg = f', notes: "{_escape_string(notes)}"' if notes else ""
        blocks.append(f'#slide("{_escape_string(title)}"{notes_arg})[\n{body_lines}\n]')
    return header + "\n\n".join(blocks) + "\n"


# Title/notes are interpolated as Typst *string* values (`#title`), which Typst
# inserts as literal text without re-parsing — so only the string-literal syntax
# itself (backslash, quote) needs escaping.
_STRING_ESCAPE = {"\\": "\\\\", '"': '\\"'}

# Bullets are written directly as markup *source* (`- <bullet>`), which Typst
# does parse — so every markup metacharacter must be neutralized, or content
# like `sigma_i^z`, `h < 1`, or `[cite]` corrupts or breaks the compile.
_MARKUP_ESCAPE = {
    "\\": "\\\\",
    "*": "\\*",
    "_": "\\_",
    "`": "\\`",
    "#": "\\#",
    "$": "\\$",
    "<": "\\<",
    ">": "\\>",
    "[": "\\[",
    "]": "\\]",
    "~": "\\~",
    "@": "\\@",
}


def _escape_string(text: str) -> str:
    return "".join(_STRING_ESCAPE.get(ch, ch) for ch in text)


def _escape_markup(text: str) -> str:
    return "".join(_MARKUP_ESCAPE.get(ch, ch) for ch in text)


def _default_typst_runner(argv: list[str]) -> str:
    proc = subprocess.run(["typst", *argv], capture_output=True, text=True)
    return proc.stderr if proc.returncode != 0 else ""


def _parse_json_object(text: str) -> dict | None:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = lines[1:-1] if lines and lines[-1].strip() == "```" else lines[1:]
        text = "\n".join(lines).strip()
    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None
