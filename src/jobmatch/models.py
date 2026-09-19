"""Domain models: skills, resumes, jobs, matches and reports."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SkillStatus = Literal["demonstrated", "listed", "adjacent", "related", "missing"]
Priority = Literal["must", "nice", "context"]
Label = Literal["strong", "possible", "weak"]
SENIORITY_NAMES = {1: "junior", 2: "mid", 3: "senior", 4: "lead or principal"}
EDUCATION_NAMES = {0: "none", 1: "associate", 2: "bachelor", 3: "master", 4: "doctorate"}


class Skill(BaseModel):
    """A skill in the taxonomy."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9+#._-]*$")
    name: str
    category: str
    aliases: tuple[str, ...] = ()
    implies: tuple[str, ...] = ()
    related: tuple[str, ...] = ()
    case_sensitive: bool = False


class Role(BaseModel):
    """One position on a resume."""

    title: str
    company: str = ""
    start: date | None = None
    end: date | None = None
    present: bool = False
    bullets: list[str] = Field(default_factory=list)
    line: int = 0

    def months(self, today: date) -> int:
        if self.start is None:
            return 0
        end = today if self.present or self.end is None else self.end
        return max(0, (end.year - self.start.year) * 12 + end.month - self.start.month + 1)


class Resume(BaseModel):
    """A parsed resume. Personal fields are kept only so they can be shown or redacted, never scored."""

    id: str
    name: str = ""
    email: str = ""
    phone: str = ""
    summary: str = ""
    roles: list[Role] = Field(default_factory=list)
    skills_listed: list[str] = Field(default_factory=list)
    education_level: int = 0
    education_lines: list[str] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    projects: list[str] = Field(default_factory=list)
    sections: list[str] = Field(default_factory=list)
    word_count: int = 0
    ignored_attributes: list[str] = Field(default_factory=list)


class Requirement(BaseModel):
    skill: str
    priority: Priority
    min_years: float | None = None


class Job(BaseModel):
    """A parsed job description."""

    id: str
    title: str
    company: str = ""
    requirements: list[Requirement] = Field(default_factory=list)
    min_years: float | None = None
    seniority: int | None = Field(default=None, ge=1, le=4)
    education_level: int | None = Field(default=None, ge=0, le=4)
    text_words: int = 0


class SkillEvidence(BaseModel):
    """What a resume shows for one skill."""

    skill: str
    listed: bool = False
    demonstrated: list[str] = Field(default_factory=list)
    years: float = 0.0
    last_used: date | None = None
    via: str = ""


class RequirementResult(BaseModel):
    skill: str
    name: str
    priority: Priority
    status: SkillStatus
    strength: float
    years: float
    required_years: float | None
    evidence: list[str]
    note: str = ""


class Component(BaseModel):
    name: str
    score: float
    weight: float
    detail: str


class MatchResult(BaseModel):
    resume_id: str
    job_id: str
    job_title: str
    score: int
    label: Label
    components: list[Component]
    requirements: list[RequirementResult]
    missing_musts: list[str]
    strengths: list[str]
    gaps: list[str]
    total_years: float
    resume_seniority: int
    ignored_attributes: list[str] = Field(default_factory=list)


class QualityFinding(BaseModel):
    code: str
    severity: Literal["info", "warning", "issue"]
    message: str
    suggestion: str = ""


class ResumeReview(BaseModel):
    resume_id: str
    findings: list[QualityFinding]
    skills_found: list[str]
    total_years: float
    quantified_bullets: int
    total_bullets: int
    keyword_gaps: list[str] = Field(default_factory=list)


class InvarianceCase(BaseModel):
    change: str
    score: int
    delta: int


class InvarianceReport(BaseModel):
    resume_id: str
    job_id: str
    baseline: int
    cases: list[InvarianceCase]
    max_abs_delta: int
    ignored_attributes: list[str]
    passed: bool


class Ranked(BaseModel):
    rank: int
    id: str
    title: str
    score: int
    label: Label
    missing_musts: list[str]
