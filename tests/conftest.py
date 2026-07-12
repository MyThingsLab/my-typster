from __future__ import annotations

import json
from pathlib import Path

import pytest

# Shared fakes come from mythings.testing (plain imports; aliased fixture
# re-export + getfixturevalue wrapper per core docs/CONVENTIONS.md).
from mythings.testing import FakeGh, GitRepo, ScriptedEngine, make_git_repo
from mythings.testing import clean_git_env as _shared_clean_git_env  # noqa: F401

__all__ = ["ScriptedEngine"]


@pytest.fixture(autouse=True)
def _clean_git_env(request: pytest.FixtureRequest) -> None:
    # Real git worktrees in every test; hook-launched pytest (pre-commit)
    # must not leak GIT_* into them.
    request.getfixturevalue("_shared_clean_git_env")


def fake_gh(
    *,
    number: int = 5,
    title: str = "Draft a report on Kernel Methods",
    body: str = "Write a report about kernel methods for SVMs.",
    labels: list[str] | None = None,
    existing_pr: dict | None = None,
) -> FakeGh:
    issue = {
        "number": number,
        "title": title,
        "body": body,
        "labels": [{"name": lbl} for lbl in (labels or [])],
    }
    return FakeGh(
        {
            ("issue", "view"): json.dumps(issue),
            ("pr", "list"): json.dumps([existing_pr] if existing_pr else []),
            ("pr", "create"): "https://github.com/owner/target/pull/7\n",
            ("issue", "comment"): "https://github.com/owner/target/issues/5#issuecomment-1\n",
        }
    )


class FakeTypst:
    # Mocks the `typst` subprocess boundary; writes a fake PDF next to the .typ
    # source on success, same as the real CLI's default output path.
    def __init__(self, *, should_fail: bool = False, error: str = "syntax error") -> None:
        self.calls: list[list[str]] = []
        self.should_fail = should_fail
        self.error = error

    def __call__(self, argv: list[str]) -> str:
        self.calls.append(argv)
        typ_path = Path(argv[-1])
        if self.should_fail:
            return self.error
        pdf_path = typ_path.with_suffix(".pdf")
        pdf_path.write_bytes(b"%PDF-1.7 fake\n")
        return ""


_TEMPLATES = {
    "default": (
        "#set text(size: 11pt)\n\n= Untitled\n\n// === body ===\nplaceholder\n"
    ),
    "report": (
        "#set text(size: 11pt)\n#set heading(numbering: \"1.1\")\n\n"
        "= Untitled Report\n\n// === body ===\n== Summary\nplaceholder\n"
    ),
    "note": ("#set text(size: 10pt)\n\n= Untitled Note\n\n// === body ===\nplaceholder\n"),
    "letter": (
        "#set text(size: 11pt)\n\n// === body ===\nDear Recipient,\n"
    ),
    "resume": (
        "#set text(size: 10pt)\n\n// === body ===\n== Experience\n"
    ),
    "presentation": (
        "#set page(paper: \"presentation-16-9\")\n\n"
        '#let slide(title, body) = [\n  = #title\n  #body\n  #pagebreak(weak: true)\n]\n\n'
        "// === body ===\n"
    ),
}


def make_templates_repo(tmp_path: Path) -> Path:
    root = tmp_path / "typst-templates"
    directory = root / "templates"
    directory.mkdir(parents=True)
    for kind, text in _TEMPLATES.items():
        (directory / f"{kind}.typ").write_text(text, encoding="utf-8")
    return root


def make_target_repo(tmp_path: Path) -> Path:
    return make_git_repo(tmp_path, files={"README.md": "# target\n"}).path


def branch_file(repo: Path, branch: str, path: str) -> str:
    return GitRepo(path=repo, origin=repo.parent / "origin.git").read_committed(branch, path)
