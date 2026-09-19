"""Scoring: how well a resume fits a job, with a breakdown a person can check.

The score is ``100 * sum(weight * component) / sum(weight)`` over the components the job actually specifies, so
a job with no education requirement does not reward or punish education. Being more senior or more educated than
required is never penalised.
"""

from __future__ import annotations

from datetime import date

from jobmatch.config import Policy
from jobmatch.errors import InputError
from jobmatch.extract import SkillExtractor, total_years
from jobmatch.fairness import scoring_view
from jobmatch.models import (
    EDUCATION_NAMES,
    SENIORITY_NAMES,
    Component,
    Job,
    Label,
    MatchResult,
    Requirement,
    RequirementResult,
    Resume,
    SkillEvidence,
)
from jobmatch.parsers import title_seniority, years_seniority
from jobmatch.taxonomy import Taxonomy


def resume_seniority(resume: Resume, years: float) -> int:
    """Seniority from experience, raised by the most recent title if that says more."""
    level = years_seniority(years) if resume.roles else 1
    dated = [r for r in resume.roles if r.start]
    latest = max(dated, key=lambda r: r.start or date.min) if dated else None
    if latest is None and resume.roles:
        latest = resume.roles[0]
    if latest is not None:
        titled = title_seniority(latest.title)
        if titled is not None:
            level = max(level, titled)
    return level


class Matcher:
    """Scores resumes against jobs. Only the scoring view of a resume is ever read."""

    def __init__(self, taxonomy: Taxonomy, policy: Policy | None = None) -> None:
        self._taxonomy = taxonomy
        self._policy = policy or Policy()
        self._extractor = SkillExtractor(taxonomy)

    @property
    def taxonomy(self) -> Taxonomy:
        return self._taxonomy

    def match(self, resume: Resume, job: Job, today: date) -> MatchResult:
        """Score ``resume`` against ``job``.

        Raises:
            InputError: If the job specifies nothing that can be scored.
        """
        view = scoring_view(resume)
        pol = self._policy
        evidence = self._extractor.extract(view, today)
        adjacent, related = self._extractor.implied(evidence)
        years = total_years(view, today)
        seniority = resume_seniority(view, years)
        wanted = {r.skill for r in job.requirements}
        relevant = self._extractor.relevant_years(view, wanted, today) if wanted else years

        rows = [
            self._requirement(req, evidence, adjacent, related, today)
            for req in job.requirements
            if req.priority != "context"
        ]
        musts = [r for r in rows if r.priority == "must"]
        nices = [r for r in rows if r.priority == "nice"]

        components: list[Component] = []
        if musts:
            score = sum(r.strength for r in musts) / len(musts)
            met = sum(1 for r in musts if r.status in ("demonstrated", "listed", "adjacent"))
            components.append(
                Component(
                    name="must-have skills",
                    score=round(score, 3),
                    weight=pol.weight_must,
                    detail=f"{met} of {len(musts)} required skills shown",
                )
            )
        if nices:
            score = sum(r.strength for r in nices) / len(nices)
            met = sum(1 for r in nices if r.status in ("demonstrated", "listed", "adjacent"))
            components.append(
                Component(
                    name="nice-to-have skills",
                    score=round(score, 3),
                    weight=pol.weight_nice,
                    detail=f"{met} of {len(nices)} preferred skills shown",
                )
            )
        if job.min_years is not None and job.min_years > 0:
            components.append(
                Component(
                    name="experience",
                    score=round(min(1.0, relevant / job.min_years), 3),
                    weight=pol.weight_experience,
                    detail=f"{relevant:g} years in relevant roles, {job.min_years:g} asked",
                )
            )
        if job.seniority is not None:
            gap = job.seniority - seniority
            components.append(
                Component(
                    name="seniority",
                    score=1.0 if gap <= 0 else max(0.0, round(1 - 0.5 * gap, 3)),
                    weight=pol.weight_seniority,
                    detail=f"{SENIORITY_NAMES[seniority]} level shown, "
                    f"{SENIORITY_NAMES[job.seniority]} asked",
                )
            )
        if job.education_level:
            have = view.education_level
            gap = job.education_level - have
            components.append(
                Component(
                    name="education",
                    score=1.0 if gap <= 0 else max(0.0, round(1 - 0.5 * gap, 3)),
                    weight=pol.weight_education,
                    detail=f"{EDUCATION_NAMES[have]} shown, {EDUCATION_NAMES[job.education_level]} asked",
                )
            )
        weights = sum(c.weight for c in components)
        if not components or weights <= 0:
            raise InputError(f"job {job.id!r} has nothing to score against")
        score_value = round(100 * sum(c.weight * c.score for c in components) / weights)

        missing = [r.name for r in musts if r.status in ("missing", "related")]
        label = self._label(score_value, missing, musts)
        strengths, gaps = self._notes(rows, job, relevant, seniority, view.education_level)
        return MatchResult(
            resume_id=resume.id,
            job_id=job.id,
            job_title=job.title,
            score=score_value,
            label=label,
            components=components,
            requirements=rows,
            missing_musts=missing,
            strengths=strengths,
            gaps=gaps,
            total_years=years,
            resume_seniority=seniority,
            ignored_attributes=list(resume.ignored_attributes),
        )

    def _label(self, score: int, missing: list[str], musts: list[RequirementResult]) -> Label:
        pol = self._policy
        must_mean = sum(r.strength for r in musts) / len(musts) if musts else 1.0
        if score < pol.possible_min or must_mean < 0.3:
            return "weak"
        if score >= pol.strong_min and not missing:
            return "strong"
        return "possible"

    def _recency(self, ev: SkillEvidence, today: date) -> float:
        pol = self._policy
        if ev.last_used is None:
            return pol.recent_factor
        months = (today.year - ev.last_used.year) * 12 + today.month - ev.last_used.month
        if months <= pol.recent_months:
            return pol.recent_factor
        if months <= pol.dated_months:
            return pol.dated_factor
        return pol.stale_factor

    def _base(self, ev: SkillEvidence, today: date) -> float:
        if ev.demonstrated:
            return self._recency(ev, today)
        return self._policy.listed_strength

    def _requirement(
        self,
        req: Requirement,
        evidence: dict[str, SkillEvidence],
        adjacent: dict[str, SkillEvidence],
        related: dict[str, SkillEvidence],
        today: date,
    ) -> RequirementResult:
        pol = self._policy
        skill = self._taxonomy.get(req.skill)
        name = skill.name if skill else req.skill
        base = {
            "skill": req.skill,
            "name": name,
            "priority": req.priority,
            "required_years": req.min_years,
        }
        direct = evidence.get(req.skill)
        if direct is not None and not direct.demonstrated:
            implied = adjacent.get(req.skill)
            if implied is not None and implied.demonstrated:
                direct = None
        if direct is not None:
            strength = self._base(direct, today)
            note = ""
            if direct.demonstrated and req.min_years and 0 < direct.years < req.min_years:
                strength *= max(0.5, direct.years / req.min_years)
                note = f"{direct.years:g} of {req.min_years:g} years"
            return RequirementResult(
                **base,
                status="demonstrated" if direct.demonstrated else "listed",
                strength=round(strength, 3),
                years=direct.years,
                evidence=direct.demonstrated,
                note=note,
            )
        for store, factor, status in (
            (adjacent, pol.implied_factor, "adjacent"),
            (related, pol.related_factor, "related"),
        ):
            hit = store.get(req.skill)
            if hit is not None:
                via = self._taxonomy.get(hit.via)
                via_name = via.name if via else hit.via
                strength = factor * self._base(hit, today)
                return RequirementResult(
                    **base,
                    status=status,
                    strength=round(strength, 3),
                    years=hit.years if status == "adjacent" else 0.0,
                    evidence=hit.demonstrated,
                    note=f"via {via_name}",
                )
        return RequirementResult(
            **base,
            status="missing",
            strength=0.0,
            years=0.0,
            evidence=[],
        )

    @staticmethod
    def _notes(
        rows: list[RequirementResult], job: Job, years: float, seniority: int, education: int
    ) -> tuple[list[str], list[str]]:
        strengths: list[str] = []
        gaps: list[str] = []
        for r in rows:
            if r.priority == "must" and r.status == "demonstrated" and r.strength >= 0.85:
                detail = f" ({r.years:g} years)" if r.years else ""
                strengths.append(f"{r.name} shown in role history{detail}")
        for r in rows:
            if r.priority != "must":
                continue
            if r.status == "missing":
                gaps.append(f"No evidence of {r.name}")
            elif r.status == "related":
                gaps.append(f"{r.name} not shown, related experience {r.note}")
            elif r.status == "listed":
                gaps.append(f"{r.name} is listed but not shown in any role")
            elif r.note and "years" in r.note:
                gaps.append(f"{r.name}: {r.note}")
        if job.min_years and years < job.min_years:
            gaps.append(f"{years:g} years in relevant roles shown, {job.min_years:g} asked")
        if job.seniority and seniority < job.seniority:
            gaps.append(
                f"{SENIORITY_NAMES[seniority]} level shown, {SENIORITY_NAMES[job.seniority]} asked"
            )
        if job.education_level and education < job.education_level:
            gaps.append(f"{EDUCATION_NAMES[job.education_level]} degree not shown")
        return strengths[:5], gaps
