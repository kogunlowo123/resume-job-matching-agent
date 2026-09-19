"""Parsers for plain-text and Markdown resumes and job descriptions, and structured job files."""

from __future__ import annotations

import json
import re
from typing import Any

import yaml

from jobmatch.dates import parse_range
from jobmatch.errors import InputError
from jobmatch.models import Job, Requirement, Resume, Role
from jobmatch.security import EMAIL, PHONE
from jobmatch.taxonomy import Taxonomy

_BULLET = re.compile(r"^\s*(?:[-*•●▪]|\d+[.)])\s+")
_HEADING = re.compile(r"^\s*(?:#{1,6}\s*)?(?P<title>[A-Za-z][A-Za-z &/]{2,40}?)\s*:?\s*$")

_RESUME_SECTIONS: dict[str, tuple[str, ...]] = {
    "summary": ("summary", "profile", "objective", "about", "about me", "professional summary"),
    "experience": (
        "experience",
        "work experience",
        "professional experience",
        "employment",
        "employment history",
        "work history",
        "career history",
    ),
    "skills": (
        "skills",
        "technical skills",
        "core skills",
        "key skills",
        "competencies",
        "technologies",
        "tools",
    ),
    "education": ("education", "academic background", "qualifications", "education and training"),
    "certifications": ("certifications", "certificates", "licenses", "licences", "training"),
    "projects": ("projects", "selected projects", "personal projects", "open source"),
}
_SECTION_LOOKUP = {alias: key for key, aliases in _RESUME_SECTIONS.items() for alias in aliases}

_JOB_MUST = (
    "requirements",
    "required",
    "must have",
    "must haves",
    "qualifications",
    "minimum qualifications",
    "required qualifications",
    "what you need",
    "what we need",
    "what you bring",
    "you have",
    "skills",
    "required skills",
    "basic qualifications",
)
_JOB_NICE = (
    "nice to have",
    "nice to haves",
    "preferred",
    "preferred qualifications",
    "bonus",
    "bonus points",
    "a plus",
    "desired",
    "desirable",
    "good to have",
    "extras",
)
_JOB_CONTEXT = (
    "responsibilities",
    "what you will do",
    "what you'll do",
    "the role",
    "about the role",
    "about us",
    "about the team",
    "about",
    "overview",
    "benefits",
    "perks",
    "why join us",
)
_NICE_CUE = re.compile(
    r"(?i)\b(?:nice to have|preferred|bonus|a plus|is a plus|desirable|ideally|advantage)\b"
)
_YEARS = re.compile(r"(?i)\b(\d{1,2})\s*(?:\+|-\s*\d{1,2}|to\s+\d{1,2})?\s*(?:or more\s+)?years?\b")

_DEGREES: tuple[tuple[int, re.Pattern[str]], ...] = (
    (
        4,
        re.compile(
            r"(?i)\b(?:ph\.?\s?d|doctorate|doctoral|d\.?phil)\b",
        ),
    ),
    (
        3,
        re.compile(
            r"(?i)\b(?:master(?:'s|s)?|m\.?sc|m\.?eng|mba|m\.?s\.?|m\.?a\.?)(?=[\s,.)]|$)",
        ),
    ),
    (
        2,
        re.compile(
            r"(?i)\b(?:bachelor(?:'s|s)?|b\.?sc|b\.?eng|b\.?tech|b\.?s\.?|b\.?a\.?)(?=[\s,.)]|$)",
        ),
    ),
    (1, re.compile(r"(?i)\b(?:associate(?:'s)?\s+degree|a\.?a\.?s\.?|diploma)\b")),
)
_DEGREE_LINE = re.compile(
    r"(?i)\b(?:degree|bachelor|master|ph\.?d|doctorate|b\.?sc|m\.?sc|mba|diploma)\b"
)
_EQUIVALENT = re.compile(r"(?i)equivalent (?:practical |work |professional )?experience")

_IGNORED_ATTRIBUTES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("date of birth", re.compile(r"(?i)\b(?:date of birth|dob|born on|birth date)\b")),
    ("age", re.compile(r"(?i)\bage\s*[:=]\s*\d{2}\b|\b\d{2}\s*years?\s*old\b")),
    (
        "marital status",
        re.compile(r"(?i)\b(?:marital status|married|single|divorced|widowed)\b\s*[:,]?"),
    ),
    ("gender", re.compile(r"(?i)\b(?:gender|sex)\s*[:=]")),
    ("nationality", re.compile(r"(?i)\b(?:nationality|citizenship|place of birth)\s*[:=]")),
    ("religion", re.compile(r"(?i)\breligion\s*[:=]")),
    ("photo", re.compile(r"(?i)\b(?:photo|photograph|headshot)\b")),
)

_SENIORITY_TITLES: tuple[tuple[int, re.Pattern[str]], ...] = (
    (
        4,
        re.compile(
            r"(?i)\b(?:principal|staff|lead|head of|director|vp|vice president|chief|architect|manager)\b"
        ),
    ),
    (3, re.compile(r"(?i)\b(?:senior|sr\.?)\b")),
    (1, re.compile(r"(?i)\b(?:junior|jr\.?|intern|entry[- ]level|graduate|trainee)\b")),
)


def title_seniority(title: str) -> int | None:
    """Seniority level implied by a job title, or ``None`` if it says nothing."""
    for level, pattern in _SENIORITY_TITLES:
        if pattern.search(title):
            return level
    return None


def years_seniority(years: float) -> int:
    if years < 2:
        return 1
    if years < 5:
        return 2
    if years < 9:
        return 3
    return 4


def education_level(text: str) -> int:
    """The highest degree level named in ``text`` (0 if none)."""
    best = 0
    for level, pattern in _DEGREES:
        if pattern.search(text):
            best = max(best, level)
    return best


def detect_ignored_attributes(text: str) -> list[str]:
    """Personal attributes present in the text. They are reported but never scored."""
    return [name for name, pattern in _IGNORED_ATTRIBUTES if pattern.search(text)]


def _heading_key(line: str) -> str | None:
    match = _HEADING.match(line)
    if not match or _BULLET.match(line):
        return None
    return _SECTION_LOOKUP.get(re.sub(r"\s+", " ", match["title"].strip().lower()))


_INLINE = re.compile(r"^\s*(?P<label>[A-Za-z][A-Za-z &/]{2,30}?)\s*:\s*(?P<rest>\S.*)$")


def _inline_section(line: str) -> tuple[str, str] | None:
    """``Skills: Python, Go`` starts the skills section and carries its first line."""
    match = _INLINE.match(line)
    if not match or _BULLET.match(line):
        return None
    key = _SECTION_LOOKUP.get(re.sub(r"\s+", " ", match["label"].strip().lower()))
    return (key, match["rest"]) if key in ("skills", "certifications", "summary") else None


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace(" ", " ")).strip(" 	-*•|,;:()#")


_SPLIT = re.compile(r"\s+at\s+|\s+@\s+|\s+\|\s+|,\s+|\s+[-–—]\s+")


def _title_company(header: str) -> tuple[str, str]:
    cleaned = _clean(header)
    parts = [p.strip() for p in _SPLIT.split(cleaned, maxsplit=1) if p.strip()]
    if not parts:
        return "", ""
    return parts[0], parts[1] if len(parts) > 1 else ""


class ResumeParser:
    """Turns resume text into a :class:`Resume`. Unknown layouts degrade to fewer fields, never to errors."""

    def __init__(self, taxonomy: Taxonomy) -> None:
        self._taxonomy = taxonomy

    def parse(self, text: str, resume_id: str) -> Resume:
        lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        sections: dict[str, list[tuple[int, str]]] = {"header": []}
        order: list[str] = []
        current = "header"
        for number, line in enumerate(lines, 1):
            key = _heading_key(line)
            inline = None if key is not None else _inline_section(line)
            if key is not None or inline is not None:
                current = key or (inline[0] if inline else "header")
                sections.setdefault(current, [])
                if current not in order:
                    order.append(current)
                if inline is not None:
                    sections[current].append((number, inline[1]))
                continue
            if line.strip():
                sections.setdefault(current, []).append((number, line))

        header = sections["header"]
        name = ""
        for _, line in header:
            stripped = line.strip().lstrip("#").strip()
            if stripped and "@" not in stripped and not re.search(r"\d{3}", stripped):
                name = stripped
                break
        email = EMAIL.search(text)
        phone = PHONE.search("\n".join(line for _, line in header)) or PHONE.search(text)

        roles = self._roles(sections.get("experience", []))
        skills_text = "\n".join(line for _, line in sections.get("skills", []))
        summary = " ".join(line.strip() for _, line in sections.get("summary", []))
        listed = self._taxonomy.mentions(skills_text)
        education_lines = [line.strip() for _, line in sections.get("education", [])]
        edu_level = education_level("\n".join(education_lines))
        certs = [_clean(line) for _, line in sections.get("certifications", []) if _clean(line)]
        projects = [_clean(line) for _, line in sections.get("projects", []) if _clean(line)]

        return Resume(
            id=resume_id,
            name=name,
            email=email.group(0) if email else "",
            phone=phone.group(0).strip() if phone else "",
            summary=summary,
            roles=roles,
            skills_listed=listed,
            education_level=edu_level,
            education_lines=education_lines,
            certifications=certs,
            projects=projects,
            sections=order,
            word_count=len(re.findall(r"\S+", text)),
            ignored_attributes=detect_ignored_attributes(text),
        )

    @staticmethod
    def _roles(entries: list[tuple[int, str]]) -> list[Role]:
        roles: list[Role] = []
        role: Role | None = None
        for number, line in entries:
            parsed = None if _BULLET.match(line) else parse_range(line)
            if parsed is not None:
                start, end, present, remainder = parsed
                title, company = _title_company(remainder)
                role = Role(
                    title=title,
                    company=company,
                    start=start,
                    end=end,
                    present=present,
                    line=number,
                )
                roles.append(role)
                continue
            text = _BULLET.sub("", line).strip()
            if not text:
                continue
            if role is None:
                title, company = _title_company(text)
                role = Role(title=title, company=company, line=number)
                roles.append(role)
            elif (
                not _BULLET.match(line) and not role.bullets and not role.company and len(text) < 60
            ):
                role.company = _clean(text)
            else:
                role.bullets.append(text)
        return roles


class JobParser:
    """Turns a job description into a :class:`Job`, separating must-have from nice-to-have skills."""

    def __init__(self, taxonomy: Taxonomy) -> None:
        self._taxonomy = taxonomy

    def parse(self, text: str, job_id: str) -> Job:
        lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        title, company = "", ""
        mode = "must"
        priorities: dict[str, str] = {}
        skill_years: dict[str, float] = {}
        general_years: list[float] = []
        edu_levels: list[int] = []
        seen_body = False
        for raw in lines:
            line = raw.strip()
            if not line:
                continue
            labelled = re.match(r"(?i)^(?:job title|title|position|role)\s*:\s*(.+)$", line)
            company_line = re.match(r"(?i)^(?:company|employer|organi[sz]ation)\s*:\s*(.+)$", line)
            if labelled and not title:
                title = _clean(labelled.group(1))
                continue
            if company_line and not company:
                company = _clean(company_line.group(1))
                continue
            if not title and not _BULLET.match(line) and not _job_heading(line):
                title = _clean(line.lstrip("#"))
                head = re.split(r"\s+at\s+|\s+@\s+|\s+\|\s+", title, maxsplit=1)
                if len(head) == 2 and not company:
                    title, company = head[0].strip(), head[1].strip()
                continue
            heading = _job_heading(line)
            if heading:
                mode = heading
                seen_body = True
                continue
            seen_body = True
            line_mode = "nice" if _NICE_CUE.search(line) else mode
            mentions = self._taxonomy.mentions(line)
            years = [float(m.group(1)) for m in _YEARS.finditer(line)]
            if len(mentions) == 1 and years:
                skill_years[mentions[0]] = max(years)
            elif years and not mentions:
                general_years.extend(years)
            elif years and len(mentions) > 1:
                general_years.append(max(years))
            for skill_id in mentions:
                current = priorities.get(skill_id)
                new = "context" if line_mode == "context" else line_mode
                if current is None or _rank(new) > _rank(current):
                    priorities[skill_id] = new
            if _DEGREE_LINE.search(line):
                level = _lowest_degree(line)
                if level and not _EQUIVALENT.search(line):
                    edu_levels.append(level)
        if not seen_body and not title:
            raise InputError(f"job {job_id!r} has no content")
        requirements = [
            Requirement(skill=s, priority=p, min_years=skill_years.get(s))
            for s, p in priorities.items()
        ]
        min_years = max(general_years) if general_years else None
        seniority = title_seniority(title)
        if seniority is None and min_years is not None:
            seniority = years_seniority(min_years)
        return Job(
            id=job_id,
            title=title or job_id,
            company=company,
            requirements=requirements,
            min_years=min_years,
            seniority=seniority,
            education_level=min(edu_levels) if edu_levels else None,
            text_words=len(re.findall(r"\S+", text)),
        )


def _rank(priority: str) -> int:
    return {"context": 0, "nice": 1, "must": 2}[priority]


def _lowest_degree(line: str) -> int:
    levels = [level for level, pattern in _DEGREES if pattern.search(line)]
    return min(levels) if levels else 0


def _job_heading(line: str) -> str | None:
    match = _HEADING.match(line)
    if not match or _BULLET.match(line):
        return None
    title = re.sub(r"\s+", " ", match["title"].strip().lower())
    if title in _JOB_NICE:
        return "nice"
    if title in _JOB_MUST:
        return "must"
    if title in _JOB_CONTEXT:
        return "context"
    return None


_SENIORITY_WORDS = {
    "junior": 1,
    "mid": 2,
    "senior": 3,
    "lead": 4,
    "principal": 4,
    "staff": 4,
}
_EDUCATION_WORDS = {
    "none": 0,
    "associate": 1,
    "bachelor": 2,
    "master": 3,
    "doctorate": 4,
    "phd": 4,
}


def job_from_data(text: str, job_id: str, taxonomy: Taxonomy) -> Job:
    """Build a :class:`Job` from a JSON or YAML document.

    Keys: ``title`` (required), ``company``, ``must`` and ``nice`` (lists of skills, each a name or
    ``{"skill": name, "years": n}`` or ``"name:years"``), ``min_years``, ``seniority``, ``education``.

    Raises:
        InputError: If the document is malformed or names a skill the taxonomy does not know.
    """
    try:
        data = json.loads(text) if text.lstrip().startswith("{") else yaml.safe_load(text)
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise InputError(f"job {job_id!r} is not valid JSON or YAML: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("title"), str) or not data["title"]:
        raise InputError(f"job {job_id!r} needs an object with a title")
    requirements: list[Requirement] = []
    seen: set[str] = set()
    for key in ("must", "nice"):
        entries = data.get(key) or []
        if not isinstance(entries, list):
            raise InputError(f"job {job_id!r}: {key} must be a list")
        for entry in entries:
            skill, years = _requirement_entry(entry, job_id)
            resolved = taxonomy.resolve(skill)
            if resolved is None:
                raise InputError(f"job {job_id!r}: unknown skill {skill!r} in {key}")
            if resolved in seen:
                continue
            seen.add(resolved)
            requirements.append(Requirement(skill=resolved, priority=key, min_years=years))
    seniority = data.get("seniority")
    if isinstance(seniority, str):
        if seniority.lower() not in _SENIORITY_WORDS:
            raise InputError(f"job {job_id!r}: unknown seniority {seniority!r}")
        seniority = _SENIORITY_WORDS[seniority.lower()]
    education = data.get("education")
    if isinstance(education, str):
        if education.lower() not in _EDUCATION_WORDS:
            raise InputError(f"job {job_id!r}: unknown education {education!r}")
        education = _EDUCATION_WORDS[education.lower()]
    try:
        return Job(
            id=str(data.get("id") or job_id),
            title=data["title"],
            company=str(data.get("company") or ""),
            requirements=requirements,
            min_years=_number(data.get("min_years"), job_id, "min_years"),
            seniority=seniority,
            education_level=education,
            text_words=len(text.split()),
        )
    except ValueError as exc:
        raise InputError(f"job {job_id!r}: {exc}") from exc


def _number(value: Any, job_id: str, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise InputError(f"job {job_id!r}: {field} must be a non-negative number")
    return float(value)


def _requirement_entry(entry: Any, job_id: str) -> tuple[str, float | None]:
    if isinstance(entry, str):
        name, _, years = entry.partition(":")
        try:
            return name.strip(), float(years) if years.strip() else None
        except ValueError as exc:
            raise InputError(f"job {job_id!r}: years in {entry!r} must be a number") from exc
    if isinstance(entry, dict) and isinstance(entry.get("skill"), str):
        return entry["skill"].strip(), _number(entry.get("years"), job_id, "years")
    raise InputError(f"job {job_id!r}: each requirement must be a name or {{skill, years}}")
