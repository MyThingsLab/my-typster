from __future__ import annotations

import json
from pathlib import Path

from mythings.ledger import Ledger

from conftest import (
    FakeTypst,
    ScriptedEngine,
    branch_file,
    fake_gh,
    make_target_repo,
    make_templates_repo,
)
from mytypster.typster import PERSONAL_REPO, Typster

_DRAFT_REPLY = json.dumps(
    {"typ_source": "#set text(size: 11pt)\n#set heading(numbering: \"1.1\")\n\n"
     "= Kernel Methods\n\n== Summary\nDrafted report body about kernel methods.\n"}
)

_FENCED_REPLY = json.dumps(
    {"typ_source": '#import "@preview/evil:1.0.0": *\n\n= Kernel Methods\nDrafted body.\n'}
)


def _keeper(
    repo: Path, templates: Path, tmp_path: Path, fake: fake_gh, **kw
) -> tuple[Typster, Ledger]:
    ledger = Ledger(tmp_path / "ledger.jsonl")
    k = Typster(
        repo_root=repo,
        repo="owner/target",
        templates_repo=templates,
        ledger=ledger,
        runner=fake,
        typst_runner=kw.pop("typst_runner", FakeTypst()),
        **kw,
    )
    return k, ledger


def test_draft_happy_path_opens_pr_and_records_ledger(tmp_path: Path) -> None:
    repo = make_target_repo(tmp_path)
    templates = make_templates_repo(tmp_path)
    fake = fake_gh()
    k, ledger = _keeper(repo, templates, tmp_path, fake, engine=ScriptedEngine(_DRAFT_REPLY))

    result = k.draft(issue=5)

    assert result.outcome == "success"
    assert result.pr == 7
    assert result.kind == "report"
    assert result.typ_path == "draft-a-report-on-kernel-methods.typ"
    assert result.pdf_path == "draft-a-report-on-kernel-methods.pdf"
    assert any(c[:2] == ["pr", "create"] for c in fake.calls)

    committed = branch_file(repo, "my-typster/5", result.typ_path)
    assert "Kernel Methods" in committed

    entry = list(ledger)[0]
    assert entry.kind == "typst_doc"
    assert entry.outcome == "success"
    assert entry.data["pr_url"] == 7


def test_draft_compile_failure_posts_comment_no_pr(tmp_path: Path) -> None:
    repo = make_target_repo(tmp_path)
    templates = make_templates_repo(tmp_path)
    fake = fake_gh()
    k, ledger = _keeper(
        repo,
        templates,
        tmp_path,
        fake,
        engine=ScriptedEngine(_DRAFT_REPLY),
        typst_runner=FakeTypst(should_fail=True, error="unexpected token"),
    )

    result = k.draft(issue=5)

    assert result.outcome == "compile_failed"
    assert not any(c[:2] == ["pr", "create"] for c in fake.calls)
    assert any(c[:2] == ["issue", "comment"] for c in fake.calls)
    comment_call = next(c for c in fake.calls if c[:2] == ["issue", "comment"])
    assert "unexpected token" in comment_call[-1]
    assert list(ledger)[0].outcome == "compile_failed"


def test_draft_rejects_over_scoped_import_and_degrades_to_stub(tmp_path: Path) -> None:
    repo = make_target_repo(tmp_path)
    templates = make_templates_repo(tmp_path)
    fake = fake_gh()
    k, ledger = _keeper(repo, templates, tmp_path, fake, engine=ScriptedEngine(_FENCED_REPLY))

    result = k.draft(issue=5)

    assert result.outcome == "success"
    committed = branch_file(repo, "my-typster/5", result.typ_path)
    assert "@preview/evil" not in committed
    assert "Write a report about kernel methods for SVMs." in committed
    assert list(ledger)[0].outcome == "success"


def test_draft_personal_kind_refused_outside_private_repo(tmp_path: Path) -> None:
    repo = make_target_repo(tmp_path)
    templates = make_templates_repo(tmp_path)
    fake = fake_gh(title="Draft my resume", body="years of experience...", labels=["resume"])
    spy = ScriptedEngine()
    k, ledger = _keeper(repo, templates, tmp_path, fake, engine=spy)

    result = k.draft(issue=5, kind="resume")

    assert result.outcome == "failure"
    assert PERSONAL_REPO in result.detail
    assert spy.calls == []  # Engine never called
    assert not any(c[:2] == ["pr", "create"] for c in fake.calls)
    assert list(ledger)[0].outcome == "failure"


def test_draft_personal_kind_allowed_in_private_repo(tmp_path: Path) -> None:
    repo = make_target_repo(tmp_path)
    templates = make_templates_repo(tmp_path)
    fake = fake_gh(title="Draft my resume", body="years of experience...", labels=["resume"])
    ledger = Ledger(tmp_path / "ledger.jsonl")
    k = Typster(
        repo_root=repo,
        repo=PERSONAL_REPO,
        templates_repo=templates,
        ledger=ledger,
        runner=fake,
        typst_runner=FakeTypst(),
        engine=ScriptedEngine(
            json.dumps({"typ_source": "#set text(size: 10pt)\n\n== Experience\n"})
        ),
    )

    result = k.draft(issue=5, kind="resume")

    assert result.outcome == "success"


def test_draft_no_pr_skips_pr_creation(tmp_path: Path) -> None:
    repo = make_target_repo(tmp_path)
    templates = make_templates_repo(tmp_path)
    fake = fake_gh()
    k, _ = _keeper(repo, templates, tmp_path, fake, engine=ScriptedEngine(_DRAFT_REPLY))

    result = k.draft(issue=5, no_pr=True)

    assert result.outcome == "success"
    assert result.pr is None
    assert not any(c[:2] == ["pr", "create"] for c in fake.calls)


def test_draft_against_noop_engine_degrades_to_header_body_stub(tmp_path: Path) -> None:
    repo = make_target_repo(tmp_path)
    templates = make_templates_repo(tmp_path)
    fake = fake_gh(title="Draft a note on RayTracer", body="A ray tracing engine.")
    k, ledger = _keeper(repo, templates, tmp_path, fake)  # default NoopEngine

    result = k.draft(issue=5)

    assert result.outcome == "success"
    assert result.kind == "note"
    committed = branch_file(repo, "my-typster/5", result.typ_path)
    assert "Draft a note on RayTracer" in committed
    assert "A ray tracing engine." in committed
    assert list(ledger)[0].outcome == "success"


def test_draft_from_json_renders_presentation_without_engine_call(tmp_path: Path) -> None:
    repo = make_target_repo(tmp_path)
    templates = make_templates_repo(tmp_path)
    fake = fake_gh(title="Deck about kernels", body="")
    spy = ScriptedEngine()
    k, ledger = _keeper(repo, templates, tmp_path, fake, engine=spy)

    slides = {"slides": [{"title": "Intro", "bullets": ["what is a kernel"]}]}
    result = k.draft(issue=5, kind="presentation", from_slides=slides)

    assert result.outcome == "success"
    assert spy.calls == []  # deterministic templating, not a second Engine call
    committed = branch_file(repo, "my-typster/5", result.typ_path)
    assert 'slide("Intro")' in committed
    assert "what is a kernel" in committed
    assert list(ledger)[0].outcome == "success"


def test_render_presentation_escapes_typst_markup_in_bullets(tmp_path: Path) -> None:
    repo = make_target_repo(tmp_path)
    templates = make_templates_repo(tmp_path)
    fake = fake_gh(title="Deck about physics", body="")
    k, ledger = _keeper(repo, templates, tmp_path, fake, engine=ScriptedEngine())

    slides = {
        "slides": [
            {
                "title": 'Field h with "quotes" \\ backslash',
                "bullets": [r"H = J \sum_{<i,j>} sigma_i^z, h ~ 1 [see #ref] $x$"],
            }
        ]
    }
    result = k.draft(issue=5, kind="presentation", from_slides=slides)

    assert result.outcome == "success"
    committed = branch_file(repo, "my-typster/5", result.typ_path)
    # bullet markup metacharacters must all be neutralized, not raw source
    # (braces/caret are plain text outside math mode, so they pass through)
    assert r"- H = J \\sum\_{\<i,j\>} sigma\_i^z, h \~ 1 \[see \#ref\] \$x\$" in committed
    # title lands inside a valid, escaped string literal
    assert '"Field h with \\"quotes\\" \\\\ backslash"' in committed
    assert list(ledger)[0].outcome == "success"


def test_render_presentation_threads_speaker_notes(tmp_path: Path) -> None:
    repo = make_target_repo(tmp_path)
    templates = make_templates_repo(tmp_path)
    fake = fake_gh(title="Deck about kernels", body="")
    k, ledger = _keeper(repo, templates, tmp_path, fake, engine=ScriptedEngine())

    slides = {
        "slides": [
            {"title": "Intro", "bullets": ["hi"], "speaker_notes": "mention the audience"},
            {"title": "No notes", "bullets": ["hi"]},
        ]
    }
    result = k.draft(issue=5, kind="presentation", from_slides=slides)

    assert result.outcome == "success"
    committed = branch_file(repo, "my-typster/5", result.typ_path)
    assert 'notes: "mention the audience"' in committed
    assert 'slide("No notes")[' in committed  # no notes -> no notes: kwarg emitted
    assert list(ledger)[0].outcome == "success"
