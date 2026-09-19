"""Resume quality review: structure, dates, evidence of impact and keyword gaps against a job."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date

from jobmatch.config import Policy
from jobmatch.extract import SkillExtractor, role_interval, total_years
from jobmatch.models import Job, QualityFinding, Resume, ResumeReview
from jobmatch.taxonomy import Taxonomy

_QUANTIFIED = re.compile(r"\d|%|\$|£|€")
_WEAK_OPENERS = (
    "responsible for",
    "worked on",
    "worked with",
    "helped",
    "assisted",
    "duties included",
    "involved in",
    "tasked with",
    "participated in",
)
_SEVERITY_ORDER = {"issue": 0, "warning": 1, "info": 2}
MAX_WORDS = 900
MIN_WORDS = 150
LONG_BULLET_WORDS = 40


class ResumeReviewer:
    """Finds problems a recruiter or applicant tracking system would trip over."""

    def __init__(self, taxonomy: Taxonomy, policy: Policy | None = None) -> None:
        self._policy = policy or Policy()
        self._taxonomy = taxonomy
        self._extractor = SkillExtractor(taxonomy)

    def review(self, resume: Resume, today: date, job: Job | None = None) -> ResumeReview:
        findings: list[QualityFinding] = []
        add = findings.append

        if not resume.email and not resume.phone:
            add(
                QualityFinding(
                    code="no_contact",
                    severity="issue",
                    message="No email address or phone number found.",
                    suggestion="Put a working email address near the top.",
                )
            )
        elif not resume.email:
            add(
                QualityFinding(
                    code="no_email", severity="warning", message="No email address found."
                )
            )

        for key, label in (
            ("experience", "work experience"),
            ("skills", "skills"),
            ("education", "education"),
        ):
            if key not in resume.sections:
                severity = "warning" if key != "education" else "info"
                add(
                    QualityFinding(
                        code=f"no_{key}_section",
                        severity=severity,
                        message=f"No clear {label} section was found.",
                        suggestion=f"Use a plain heading such as '{key.title()}' so parsers find it.",
                    )
                )
        if not resume.summary:
            add(
                QualityFinding(
                    code="no_summary",
                    severity="info",
                    message="No summary or profile section.",
                    suggestion="Two lines on your focus and strongest skills help a first read.",
                )
            )
        if resume.word_count > MAX_WORDS:
            add(
                QualityFinding(
                    code="too_long",
                    severity="info",
                    message=f"{resume.word_count} words is long for a resume.",
                    suggestion="Trim older or less relevant roles.",
                )
            )
        elif resume.word_count < MIN_WORDS:
            add(
                QualityFinding(
                    code="too_short",
                    severity="warning",
                    message=f"Only {resume.word_count} words. There is little to score.",
                    suggestion="Add concrete detail to each role.",
                )
            )

        self._dates(resume, today, add)

        bullets = [b for r in resume.roles for b in r.bullets]
        quantified = sum(1 for b in bullets if _QUANTIFIED.search(b))
        if bullets and quantified / len(bullets) < self._policy.quantified_ratio_min:
            add(
                QualityFinding(
                    code="few_numbers",
                    severity="warning",
                    message=f"{quantified} of {len(bullets)} bullets contain a number or measurement.",
                    suggestion="Add scale, speed, cost or volume where you can state it accurately.",
                )
            )
        weak = [b for b in bullets if b.lower().startswith(_WEAK_OPENERS)]
        if weak:
            add(
                QualityFinding(
                    code="weak_verbs",
                    severity="info",
                    message=f"{len(weak)} bullets open with a vague phrase such as 'responsible for'.",
                    suggestion="Start with what you did: built, cut, led, shipped.",
                )
            )
        long_bullets = [b for b in bullets if len(b.split()) > LONG_BULLET_WORDS]
        if long_bullets:
            add(
                QualityFinding(
                    code="long_bullets",
                    severity="info",
                    message=f"{len(long_bullets)} bullets run past {LONG_BULLET_WORDS} words.",
                    suggestion="Split them so each carries one accomplishment.",
                )
            )
        for role in resume.roles:
            if not role.bullets and role.title:
                add(
                    QualityFinding(
                        code="empty_role",
                        severity="warning",
                        message=f"Role '{role.title}' has no description.",
                        suggestion="Add two or three bullets on what you did there.",
                    )
                )

        evidence = self._extractor.extract(resume, today)
        listed_only = sorted(k for k, ev in evidence.items() if ev.listed and not ev.demonstrated)
        if listed_only:
            add(
                QualityFinding(
                    code="listed_not_shown",
                    severity="info",
                    message=f"{len(listed_only)} skills are listed but never used in a role bullet.",
                    suggestion="Show at least the important ones in context.",
                )
            )
        gaps: list[str] = []
        if job is not None:
            adjacent, _ = self._extractor.implied(evidence)
            for req in job.requirements:
                if req.skill not in evidence and req.skill not in adjacent:
                    skill = self._taxonomy.get(req.skill)
                    gaps.append(skill.name if skill else req.skill)
            if gaps:
                add(
                    QualityFinding(
                        code="keyword_gaps",
                        severity="info",
                        message=f"{len(gaps)} skills in the job do not appear on the resume.",
                        suggestion="Add one only if you have really used it.",
                    )
                )

        findings.sort(key=lambda f: (_SEVERITY_ORDER[f.severity], f.code))
        return ResumeReview(
            resume_id=resume.id,
            findings=findings,
            skills_found=sorted(evidence),
            total_years=total_years(resume, today),
            quantified_bullets=quantified,
            total_bullets=len(bullets),
            keyword_gaps=gaps,
        )

    def _dates(self, resume: Resume, today: date, add: Callable[[QualityFinding], None]) -> None:
        undated = [r for r in resume.roles if r.start is None and r.title]
        if undated:
            add(
                QualityFinding(
                    code="undated_roles",
                    severity="warning",
                    message=f"{len(undated)} roles have no readable dates, so their years are not counted.",
                    suggestion="Write dates as 'Jan 2020 - Mar 2023'.",
                )
            )
        for role in resume.roles:
            if role.start and role.end and not role.present and role.end < role.start:
                add(
                    QualityFinding(
                        code="date_order",
                        severity="issue",
                        message=f"Role '{role.title}' ends before it starts.",
                        suggestion="Check the dates.",
                    )
                )
            if role.start and role.start > today:
                add(
                    QualityFinding(
                        code="future_date",
                        severity="issue",
                        message=f"Role '{role.title}' starts in the future.",
                        suggestion="Check the dates.",
                    )
                )
        intervals = sorted(i for r in resume.roles if (i := role_interval(r, today)) is not None)
        latest_end = -1
        for start, end in intervals:
            gap = start - latest_end - 1
            if latest_end >= 0 and gap > self._policy.max_gap_months:
                add(
                    QualityFinding(
                        code="gap",
                        severity="info",
                        message=f"A gap of about {gap} months between roles.",
                        suggestion="A short note on what you did in that time avoids guesswork.",
                    )
                )
            latest_end = max(latest_end, end)
