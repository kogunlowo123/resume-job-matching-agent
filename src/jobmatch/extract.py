"""Evidence-based skill extraction.

A skill mentioned in a role's title or bullets is *demonstrated* and carries the years of that role. A skill that
only appears in a skills list or summary is *listed*. Skills implied by demonstrated ones (Kubernetes implies
containers) are recorded as adjacent evidence.
"""

from __future__ import annotations

from datetime import date

from jobmatch.dates import month_index
from jobmatch.models import Resume, Role, SkillEvidence
from jobmatch.security import scrub_pii
from jobmatch.taxonomy import Taxonomy

MAX_QUOTE_CHARS = 160
MAX_QUOTES = 3


def merged_months(intervals: list[tuple[int, int]]) -> int:
    """Total months covered by possibly overlapping ``(start, end)`` month-index intervals."""
    total = 0
    last_end = -1
    for start, end in sorted(intervals):
        start = max(start, last_end + 1)
        if end >= start:
            total += end - start + 1
            last_end = end
    return total


def role_interval(role: Role, today: date) -> tuple[int, int] | None:
    if role.start is None:
        return None
    end = today if role.present or role.end is None else role.end
    start_index, end_index = month_index(role.start), month_index(end)
    return (start_index, end_index) if end_index >= start_index else None


def total_years(resume: Resume, today: date) -> float:
    intervals = [i for r in resume.roles if (i := role_interval(r, today))]
    return round(merged_months(intervals) / 12, 1)


def _quote(text: str) -> str:
    cleaned = scrub_pii(" ".join(text.split()))
    return cleaned if len(cleaned) <= MAX_QUOTE_CHARS else cleaned[: MAX_QUOTE_CHARS - 1] + "…"


class SkillExtractor:
    """Builds :class:`SkillEvidence` for every skill a resume shows."""

    def __init__(self, taxonomy: Taxonomy) -> None:
        self._taxonomy = taxonomy

    def extract(self, resume: Resume, today: date) -> dict[str, SkillEvidence]:
        found: dict[str, SkillEvidence] = {}
        intervals: dict[str, list[tuple[int, int]]] = {}
        for role in resume.roles:
            span = role_interval(role, today)
            lines = [f"Title: {role.title}", *role.bullets]
            for line in lines:
                for skill_id in self._taxonomy.mentions(line):
                    ev = found.setdefault(skill_id, SkillEvidence(skill=skill_id))
                    quote = _quote(line)
                    if quote not in ev.demonstrated and len(ev.demonstrated) < MAX_QUOTES:
                        ev.demonstrated.append(quote)
                    if span:
                        intervals.setdefault(skill_id, []).append(span)
                        end_date = today if role.present or role.end is None else role.end
                        if ev.last_used is None or end_date > ev.last_used:
                            ev.last_used = end_date
        for skill_id, spans in intervals.items():
            found[skill_id].years = round(merged_months(spans) / 12, 1)

        for skill_id in [*resume.skills_listed, *self._taxonomy.mentions(resume.summary)]:
            found.setdefault(skill_id, SkillEvidence(skill=skill_id)).listed = True
        for skill_id in self._taxonomy.mentions("\n".join(resume.certifications)):
            found.setdefault(skill_id, SkillEvidence(skill=skill_id)).listed = True
        return found

    def relevant_years(self, resume: Resume, wanted: set[str], today: date) -> float:
        """Years covered by roles that show at least one wanted skill, directly or by implication."""
        intervals: list[tuple[int, int]] = []
        for role in resume.roles:
            span = role_interval(role, today)
            if span is None:
                continue
            shown: set[str] = set()
            for line in [role.title, *role.bullets]:
                for skill_id in self._taxonomy.mentions(line):
                    shown.add(skill_id)
                    shown.update(self._closure(skill_id))
            if shown & wanted:
                intervals.append(span)
        return round(merged_months(intervals) / 12, 1)

    def implied(
        self, evidence: dict[str, SkillEvidence]
    ) -> tuple[dict[str, SkillEvidence], dict[str, SkillEvidence]]:
        """Skills implied by, and related to, what the resume shows.

        Each value is a copy of the strongest source's evidence with ``via`` naming that source. A skill the
        resume demonstrates directly is never listed here.
        """
        adjacent: dict[str, SkillEvidence] = {}
        related: dict[str, SkillEvidence] = {}
        for source_id, source in evidence.items():
            skill = self._taxonomy.get(source_id)
            if skill is None:
                continue
            for target in self._closure(source_id):
                if target not in evidence or not evidence[target].demonstrated:
                    self._keep(adjacent, target, source)
            for target in skill.related:
                if target not in evidence:
                    self._keep(related, target, source)
        for target in list(related):
            if target in adjacent:
                del related[target]
        return adjacent, related

    def _closure(self, skill_id: str) -> list[str]:
        seen: list[str] = []
        stack = [skill_id]
        while stack:
            skill = self._taxonomy.get(stack.pop())
            if skill is None:
                continue
            for nxt in skill.implies:
                if nxt not in seen and nxt != skill_id:
                    seen.append(nxt)
                    stack.append(nxt)
        return seen

    @staticmethod
    def _keep(store: dict[str, SkillEvidence], target: str, source: SkillEvidence) -> None:
        rank = (bool(source.demonstrated), source.years)
        current = store.get(target)
        if current is not None and (bool(current.demonstrated), current.years) >= rank:
            return
        store[target] = SkillEvidence(
            skill=target,
            listed=source.listed and not source.demonstrated,
            demonstrated=list(source.demonstrated[:1]),
            years=source.years,
            last_used=source.last_used,
            via=source.skill,
        )
