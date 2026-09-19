"""Fairness safeguards.

Scoring reads a *view* of the resume with names, contact details, employer and school names and education lines
removed. ``perturbations`` builds variants of a resume's text that change only those attributes, so ``audit``
can show that the score does not move.
"""

from __future__ import annotations

import re

from jobmatch.models import Resume
from jobmatch.security import EMAIL, PHONE

_BULLET = re.compile(r"^\s*(?:[-*•●▪]|\d+[.)])\s+")
_SCHOOL = re.compile(
    r"(?i)\b(?:(?:university|college|institute|school|academy)(?:\s+of\s+[A-Z][\w&' -]*)?"
    r"|[A-Z][\w&'-]*(?:\s+[A-Z][\w&'-]*)*\s+(?:university|college|institute|school|academy))"
)
_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
_ALT_NAMES = ("Priya Raman", "Tunde Bakare", "Maria Gonzalez", "Wei Zhang")


def scoring_view(resume: Resume) -> Resume:
    """The only parts of a resume the scorer may read."""
    view = resume.model_copy(deep=True)
    view.name = ""
    view.email = ""
    view.phone = ""
    view.education_lines = []
    view.ignored_attributes = []
    for role in view.roles:
        role.company = ""
    return view


def _map_non_bullets(text: str, repl: str, needles: list[str]) -> str:
    out: list[str] = []
    for line in text.split("\n"):
        if _BULLET.match(line):
            out.append(line)
            continue
        for needle in needles:
            if needle:
                line = line.replace(needle, repl)
        out.append(line)
    return "\n".join(out)


def perturbations(resume: Resume, text: str) -> list[tuple[str, str]]:
    """Variants of ``text`` that alter only attributes the scorer must ignore."""
    variants: list[tuple[str, str]] = []
    lines = text.split("\n")
    first = next((i for i, ln in enumerate(lines) if ln.strip()), None)

    for alt in _ALT_NAMES[:2]:
        if first is not None and resume.name:
            changed = list(lines)
            changed[first] = changed[first].replace(resume.name, alt)
            variants.append((f"name changed to {alt}", "\n".join(changed)))

    contact = EMAIL.sub("someone@example.org", text)
    contact = PHONE.sub("+1 202 555 0100", contact) if resume.phone else contact
    if contact != text:
        variants.append(("email and phone replaced", contact))

    companies = sorted({r.company for r in resume.roles if r.company}, key=len, reverse=True)
    if companies:
        variants.append(("employer names replaced", _map_non_bullets(text, "Employer", companies)))

    edu_text = "\n".join(resume.education_lines)
    schools = sorted({m.group(0) for m in _SCHOOL.finditer(edu_text)}, key=len, reverse=True)
    if schools:
        variants.append(
            ("school names replaced", _map_non_bullets(text, "Example University", schools))
        )

    if resume.education_lines:
        shifted = text
        for line in resume.education_lines:
            years = _YEAR.findall(line)
            if years:
                new = _YEAR.sub(lambda m: str(int(m.group(0)) - 7), line)
                shifted = shifted.replace(line, new, 1)
        if shifted != text:
            variants.append(("graduation years shifted by 7", shifted))

    extra = "Date of birth: 1971-03-02\nMarital status: married\nGender: female"
    variants.append(("personal attributes added to header", extra + "\n" + text))
    return variants
