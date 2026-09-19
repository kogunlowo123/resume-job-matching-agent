"""Report rendering: Markdown for people, JSON for machines, CSV for rankings."""

from __future__ import annotations

import csv
import io

from pydantic import BaseModel

from jobmatch.models import (
    InvarianceReport,
    MatchResult,
    Ranked,
    ResumeReview,
    Skill,
)
from jobmatch.security import csv_safe, md_cell, md_code

FORMATS = ("md", "json", "csv")
_STATUS = {
    "demonstrated": "shown in role",
    "listed": "listed only",
    "adjacent": "implied",
    "related": "related only",
    "missing": "not shown",
}


def render_json(model: BaseModel | list[Ranked]) -> str:
    if isinstance(model, list):
        return "[" + ",".join(m.model_dump_json() for m in model) + "]"
    return model.model_dump_json(indent=2)


def render_match_md(result: MatchResult, summary: str = "") -> str:
    lines = [
        f"# Match: {md_cell(result.resume_id)} for {md_cell(result.job_title)}",
        "",
        f"**Score {result.score} of 100, {result.label} match.** "
        f"{result.total_years:g} years of dated experience shown.",
        "",
    ]
    if summary:
        lines += [md_cell(summary), ""]
    lines += [
        "## Breakdown",
        "",
        "| Component | Score | Weight | Detail |",
        "| --- | ---: | ---: | --- |",
    ]
    for c in result.components:
        lines.append(
            f"| {md_cell(c.name)} | {round(c.score * 100)} | {c.weight:g} | {md_cell(c.detail)} |"
        )
    lines += [
        "",
        "## Requirements",
        "",
        "| Skill | Priority | Status | Strength | Evidence |",
        "| --- | --- | --- | ---: | --- |",
    ]
    for r in result.requirements:
        note = f" ({md_cell(r.note)})" if r.note else ""
        quote = md_code(r.evidence[0]) if r.evidence else "-"
        lines.append(
            f"| {md_cell(r.name)} | {r.priority} | {_STATUS[r.status]}{note} | {round(r.strength * 100)} | {quote} |"
        )
    if result.strengths:
        lines += ["", "## Strengths", ""] + [f"- {md_cell(s)}" for s in result.strengths]
    if result.gaps:
        lines += ["", "## Gaps", ""] + [f"- {md_cell(g)}" for g in result.gaps]
    if result.ignored_attributes:
        lines += [
            "",
            "## Ignored attributes",
            "",
            "The resume mentions "
            + ", ".join(md_cell(a) for a in result.ignored_attributes)
            + ". These were not used in scoring.",
        ]
    return "\n".join(lines) + "\n"


def render_ranking_md(rows: list[Ranked], heading: str) -> str:
    lines = [
        f"# {md_cell(heading)}",
        "",
        "| Rank | Name | Score | Match | Missing required |",
        "| ---: | --- | ---: | --- | --- |",
    ]
    for r in rows:
        missing = ", ".join(md_cell(m) for m in r.missing_musts) or "-"
        name = f"{md_cell(r.title)} ({md_cell(r.id)})" if r.title else md_cell(r.id)
        lines.append(f"| {r.rank} | {name} | {r.score} | {r.label} | {missing} |")
    return "\n".join(lines) + "\n"


def render_ranking_csv(rows: list[Ranked]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["rank", "id", "title", "score", "label", "missing_musts"])
    for r in rows:
        writer.writerow(
            [
                r.rank,
                csv_safe(r.id),
                csv_safe(r.title),
                r.score,
                r.label,
                csv_safe("; ".join(r.missing_musts)),
            ]
        )
    return buffer.getvalue()


def render_review_md(review: ResumeReview) -> str:
    lines = [
        f"# Resume review: {md_cell(review.resume_id)}",
        "",
        f"{len(review.skills_found)} skills found, {review.total_years:g} years of dated experience, "
        f"{review.quantified_bullets} of {review.total_bullets} bullets quantified.",
        "",
    ]
    if not review.findings:
        lines.append("No findings.")
    for f in review.findings:
        tip = f" {md_cell(f.suggestion)}" if f.suggestion else ""
        lines.append(f"- **{f.severity}**: {md_cell(f.message)}{tip}")
    if review.keyword_gaps:
        lines += ["", "## Keywords in the job that the resume lacks", ""]
        lines.append(", ".join(md_cell(g) for g in review.keyword_gaps))
        lines.append("")
        lines.append("Add a skill only if you have really used it.")
    return "\n".join(lines) + "\n"


def render_audit_md(report: InvarianceReport) -> str:
    verdict = "PASS" if report.passed else "FAIL"
    lines = [
        f"# Invariance audit: {md_cell(report.resume_id)} for {md_cell(report.job_id)}",
        "",
        f"**{verdict}.** Baseline score {report.baseline}. Largest change {report.max_abs_delta}.",
        "",
        "| Change to the resume | Score | Delta |",
        "| --- | ---: | ---: |",
    ]
    for c in report.cases:
        lines.append(f"| {md_cell(c.change)} | {c.score} | {c.delta:+d} |")
    if report.ignored_attributes:
        lines += [
            "",
            "The original resume mentions "
            + ", ".join(md_cell(a) for a in report.ignored_attributes)
            + ".",
        ]
    return "\n".join(lines) + "\n"


def render_skills_md(skills: list[Skill]) -> str:
    lines = ["| Skill | Category | Aliases | Implies |", "| --- | --- | --- | --- |"]
    for s in skills:
        lines.append(
            f"| {md_cell(s.name)} | {s.category} | {md_cell(', '.join(s.aliases)) or '-'} | {md_cell(', '.join(s.implies)) or '-'} |"
        )
    return "\n".join(lines) + "\n"
