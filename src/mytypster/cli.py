from __future__ import annotations

import argparse
import json
from pathlib import Path

from mythings.engine import ClaudeCLIEngine, Engine, NoopEngine
from mythings.ledger import Ledger

from mytypster.typster import Result, Typster


def build_engine(name: str, *, model: str | None = None) -> Engine:
    if name == "claude-cli":
        return ClaudeCLIEngine(model=model)
    return NoopEngine()


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo", help="GitHub slug owner/name where the issue lives")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd(), help="local git repo")
    parser.add_argument(
        "--templates-repo",
        type=Path,
        default=Path("../typst-templates"),
        help="local checkout of the shared typst-templates repo (default: ../typst-templates)",
    )
    parser.add_argument("--base", default="main", help="base branch for the PR")
    parser.add_argument("--ledger", type=Path, default=Path(".mythings/ledger.jsonl"))
    parser.add_argument("--no-pr", action="store_true", help="skip opening the drafted document PR")
    parser.add_argument(
        "--engine",
        choices=("noop", "claude-cli"),
        default="noop",
        help="Engine backend for drafting (default: noop — emits a header/body stub)",
    )
    parser.add_argument("--engine-model", help="model for --engine claude-cli")
    parser.add_argument("--json", action="store_true", help="print the result as JSON")


def _render(result: Result) -> str:
    line = f"{result.outcome}: {result.detail}"
    if result.pr is not None:
        line += f" — PR #{result.pr}"
    if result.typ_path:
        line += f" [{result.typ_path}]"
    return line


def _make(args: argparse.Namespace) -> Typster:
    return Typster(
        repo_root=args.repo_root,
        repo=args.repo,
        templates_repo=args.templates_repo,
        ledger=Ledger(args.ledger),
        base=args.base,
        engine=build_engine(args.engine, model=args.engine_model),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mytypster",
        description="Draft and typeset a document (Typst source, compiled PDF) from an issue.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    draft = sub.add_parser("draft", help="draft and compile a document for one issue")
    _add_common(draft)
    draft.add_argument("--issue", type=int, required=True, help="the document-request issue")
    draft.add_argument("--kind", help="report|note|letter|resume|... (default: inferred)")
    draft.add_argument(
        "--from-json",
        type=Path,
        help="skip the Engine call; deterministically render this slide-data JSON "
        "file instead (MyPresentation's hand-off point)",
    )

    args = parser.parse_args(argv)
    keeper = _make(args)

    from_slides = None
    if args.from_json is not None:
        from_slides = json.loads(args.from_json.read_text(encoding="utf-8"))

    result = keeper.draft(args.issue, kind=args.kind, no_pr=args.no_pr, from_slides=from_slides)

    if args.json:
        print(json.dumps(result.to_json()))
    else:
        print(_render(result))
    return 1 if result.outcome in {"failure", "compile_failed"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
