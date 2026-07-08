from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from mythings.engine import EngineRequest, EngineResult


@pytest.fixture(autouse=True)
def _clean_git_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # pre-commit runs hooks with GIT_DIR/GIT_INDEX_FILE set; they leak into the
    # git subprocesses these tests spawn (and into isolation.Workspace) and break
    # worktree ops on the throwaway repo. Real runs are not inside a hook.
    for var in ("GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE", "GIT_OBJECT_DIRECTORY"):
        monkeypatch.delenv(var, raising=False)


class ScriptedEngine:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls: list[EngineRequest] = []

    def run(self, request: EngineRequest) -> EngineResult:
        self.calls.append(request)
        return EngineResult(text=self.reply)


class SpyEngine:
    def __init__(self) -> None:
        self.calls: list[EngineRequest] = []

    def run(self, request: EngineRequest) -> EngineResult:
        self.calls.append(request)
        return EngineResult(text="")


class FakeRunner:
    # Mocks only the `gh` subprocess boundary.
    def __init__(
        self,
        *,
        number: int = 5,
        title: str = "Draft a report on Kernel Methods",
        body: str = "Write a report about kernel methods for SVMs.",
        labels: list[str] | None = None,
        existing_pr: dict | None = None,
    ) -> None:
        self.calls: list[list[str]] = []
        self.number = number
        self.title = title
        self.body = body
        self.labels = labels or []
        self.existing_pr = existing_pr

    def __call__(self, argv: list[str]) -> str:
        self.calls.append(argv)
        if argv[:2] == ["issue", "view"]:
            return json.dumps(
                {
                    "number": self.number,
                    "title": self.title,
                    "body": self.body,
                    "labels": [{"name": lbl} for lbl in self.labels],
                }
            )
        if argv[:2] == ["pr", "list"]:
            return json.dumps([self.existing_pr] if self.existing_pr else [])
        if argv[:2] == ["pr", "create"]:
            return "https://github.com/owner/target/pull/7\n"
        if argv[:2] == ["issue", "comment"]:
            return "https://github.com/owner/target/issues/5#issuecomment-1\n"
        raise AssertionError(f"unexpected gh call: {argv}")


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
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", str(origin)], check=True, capture_output=True)
    repo = tmp_path / "work"
    repo.mkdir()
    (repo / "README.md").write_text("# target\n", encoding="utf-8")

    def _git(*argv: str) -> None:
        subprocess.run(["git", "-C", str(repo), *argv], check=True, capture_output=True, text=True)

    _git("init", "-b", "main")
    _git("config", "user.email", "t@example.com")
    _git("config", "user.name", "Typster")
    _git("add", "-A")
    _git("commit", "-m", "init")
    _git("remote", "add", "origin", str(origin))
    _git("push", "-u", "origin", "main")
    return repo


def branch_file(repo: Path, branch: str, path: str) -> str:
    origin = repo.parent / "origin.git"
    proc = subprocess.run(
        ["git", "-C", str(origin), "show", f"{branch}:{path}"],
        capture_output=True,
        text=True,
    )
    return proc.stdout
