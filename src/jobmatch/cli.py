"""Command-line interface ``jobmatch``.

Exit codes: 0 success, 1 a gate failed (``--min-score`` or a failed audit), 2 invalid input or a runtime error.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import date, datetime, timezone
from pathlib import Path

from pydantic import BaseModel, ValidationError

from jobmatch.config import Settings
from jobmatch.container import build_service
from jobmatch.errors import MatchError
from jobmatch.logging_setup import configure_logging
from jobmatch.models import Ranked
from jobmatch.reporting import (
    FORMATS,
    render_audit_md,
    render_json,
    render_match_md,
    render_ranking_csv,
    render_ranking_md,
    render_review_md,
    render_skills_md,
)
from jobmatch.security import redact
from jobmatch.service import MatchService


def _date(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid date {text!r}; use YYYY-MM-DD") from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jobmatch", description="Explainable resume and job matching"
    )
    parser.add_argument("--as-of", type=_date, help="reference date, YYYY-MM-DD (default: today)")
    sub = parser.add_subparsers(dest="command", required=True)

    def fmt(p: argparse.ArgumentParser, default: str = "md") -> None:
        p.add_argument("--format", choices=FORMATS, default=default)
        p.add_argument("--output", type=Path, help="write the report to this file")

    match = sub.add_parser("match", help="score one resume against one job")
    match.add_argument("resume", type=Path)
    match.add_argument("job", type=Path)
    match.add_argument("--summary", action="store_true", help="add a short narrative summary")
    match.add_argument("--min-score", type=int, help="exit 1 if the score is below this")
    fmt(match)

    rc = sub.add_parser("rank-candidates", help="rank a folder of resumes for one job")
    rc.add_argument("resumes", type=Path, help="folder of .txt or .md resumes")
    rc.add_argument("job", type=Path)
    rc.add_argument("--top", type=int)
    fmt(rc)

    rj = sub.add_parser("rank-jobs", help="rank a folder of jobs for one resume")
    rj.add_argument("resume", type=Path)
    rj.add_argument("jobs", type=Path, help="folder of job files")
    rj.add_argument("--top", type=int)
    fmt(rj)

    analyze = sub.add_parser("analyze", help="review a resume's structure and evidence")
    analyze.add_argument("resume", type=Path)
    analyze.add_argument(
        "--job", type=Path, help="also list keywords the resume lacks for this job"
    )
    fmt(analyze)

    audit = sub.add_parser("audit", help="check that personal attributes do not change the score")
    audit.add_argument("resume", type=Path)
    audit.add_argument("job", type=Path)
    fmt(audit)

    skills = sub.add_parser("skills", help="list or search the skills taxonomy")
    skills.add_argument("query", nargs="?", default="")

    parse = sub.add_parser("parse", help="show how a resume or job file is parsed (JSON)")
    parse.add_argument("kind", choices=("resume", "job"))
    parse.add_argument("path", type=Path)
    return parser


def _emit(text: str, output: Path | None) -> None:
    if output is None:
        print(text, end="" if text.endswith("\n") else "\n")
        return
    output.write_text(text, encoding="utf-8")
    print(f"wrote {output.name}", file=sys.stderr)


def _render(model: BaseModel | list[Ranked], fmt: str, md: str, csv_text: str | None = None) -> str:
    if fmt == "json":
        return render_json(model)
    if fmt == "csv":
        if csv_text is None:
            raise MatchError("csv output is available for rankings only")
        return csv_text
    return md


def _run(args: argparse.Namespace, service: MatchService, today: date) -> int:
    cmd = args.command
    if cmd == "skills":
        found = service.taxonomy.search(args.query) if args.query else list(service.taxonomy)
        print(render_skills_md(found), end="")
        return 0
    if cmd == "parse":
        parsed = (
            service.load_resume(args.path) if args.kind == "resume" else service.load_job(args.path)
        )
        print(parsed.model_dump_json(indent=2, exclude={"email", "phone"}))
        return 0
    if cmd == "match":
        result = service.match(service.load_resume(args.resume), service.load_job(args.job), today)
        summary = service.summarize(result) if args.summary else ""
        _emit(_render(result, args.format, render_match_md(result, summary)), args.output)
        if args.min_score is not None and result.score < args.min_score:
            return 1
        return 0
    if cmd == "rank-candidates":
        ranked = service.rank_candidates(
            service.load_resumes(args.resumes), service.load_job(args.job), today, args.top
        )
        heading = f"Candidates for {args.job.stem}"
        _emit(
            _render(
                ranked, args.format, render_ranking_md(ranked, heading), render_ranking_csv(ranked)
            ),
            args.output,
        )
        return 0
    if cmd == "rank-jobs":
        ranked = service.rank_jobs(
            service.load_resume(args.resume), service.load_jobs(args.jobs), today, args.top
        )
        heading = f"Jobs for {args.resume.stem}"
        _emit(
            _render(
                ranked, args.format, render_ranking_md(ranked, heading), render_ranking_csv(ranked)
            ),
            args.output,
        )
        return 0
    if cmd == "analyze":
        job = service.load_job(args.job) if args.job else None
        review = service.review(service.load_resume(args.resume), today, job)
        _emit(_render(review, args.format, render_review_md(review)), args.output)
        return 0
    if args.resume.suffix.lower() not in (".txt", ".md"):
        raise MatchError(f"{args.resume.name}: resumes must be .txt or .md files")
    report = service.audit(
        service.read_text(args.resume), args.resume.stem, service.load_job(args.job), today
    )
    _emit(_render(report, args.format, render_audit_md(report)), args.output)
    return 0 if report.passed else 1


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. Returns a process exit code."""
    args = _parser().parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    try:
        settings = Settings()
        configure_logging(settings.log_level, json_output=settings.log_json)
        service = build_service(settings)
        today = args.as_of or datetime.now(timezone.utc).date()
        return _run(args, service, today)
    except (MatchError, ValidationError) as exc:
        print(f"error: {redact(str(exc))}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
