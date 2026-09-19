"""Application service: loads documents and runs matching, ranking, review and audit."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from jobmatch.analysis import ResumeReviewer
from jobmatch.config import Policy, Settings, load_policy
from jobmatch.errors import InputError
from jobmatch.fairness import perturbations
from jobmatch.logging_setup import get_logger
from jobmatch.matching import Matcher
from jobmatch.models import (
    InvarianceCase,
    InvarianceReport,
    Job,
    MatchResult,
    Ranked,
    Resume,
    ResumeReview,
)
from jobmatch.parsers import JobParser, ResumeParser, job_from_data
from jobmatch.summary import SummaryWriter, facts_for
from jobmatch.taxonomy import Taxonomy, load_taxonomy

_log = get_logger("service")
RESUME_SUFFIXES = (".txt", ".md")
JOB_SUFFIXES = (".txt", ".md", ".json", ".yaml", ".yml")


class MatchService:
    """Facade over parsing, scoring, ranking, review and the invariance audit."""

    def __init__(
        self,
        settings: Settings,
        writer: SummaryWriter,
        *,
        taxonomy: Taxonomy | None = None,
        policy: Policy | None = None,
    ) -> None:
        self._settings = settings
        self._writer = writer
        self._taxonomy = taxonomy or load_taxonomy(settings.taxonomy_file)
        self._policy = policy or load_policy(settings.policy_file)
        self._resumes = ResumeParser(self._taxonomy)
        self._jobs = JobParser(self._taxonomy)
        self._matcher = Matcher(self._taxonomy, self._policy)
        self._reviewer = ResumeReviewer(self._taxonomy, self._policy)

    @property
    def taxonomy(self) -> Taxonomy:
        return self._taxonomy

    def read_text(self, path: Path) -> str:
        """Read a document with a size limit.

        Raises:
            InputError: If the file is missing, unreadable, too large or not text.
        """
        try:
            size = path.stat().st_size
            if size > self._settings.max_document_bytes:
                raise InputError(
                    f"{path.name} is larger than {self._settings.max_document_bytes} bytes"
                )
            return path.read_text(encoding="utf-8-sig")
        except OSError as exc:
            raise InputError(f"cannot read {path.name}: {exc.strerror or exc}") from exc
        except UnicodeDecodeError as exc:
            raise InputError(f"{path.name} is not UTF-8 text") from exc

    def parse_resume(self, text: str, resume_id: str) -> Resume:
        return self._resumes.parse(text, resume_id)

    def parse_job(self, text: str, job_id: str, *, structured: bool = False) -> Job:
        if structured:
            return job_from_data(text, job_id, self._taxonomy)
        return self._jobs.parse(text, job_id)

    def load_resume(self, path: Path) -> Resume:
        if path.suffix.lower() not in RESUME_SUFFIXES:
            raise InputError(f"{path.name}: resumes must be .txt or .md files")
        return self.parse_resume(self.read_text(path), path.stem)

    def load_job(self, path: Path) -> Job:
        suffix = path.suffix.lower()
        if suffix not in JOB_SUFFIXES:
            raise InputError(f"{path.name}: jobs must be one of {', '.join(JOB_SUFFIXES)}")
        return self.parse_job(
            self.read_text(path), path.stem, structured=suffix in (".json", ".yaml", ".yml")
        )

    def _files(self, directory: Path, suffixes: tuple[str, ...]) -> list[Path]:
        if not directory.is_dir():
            raise InputError(f"{directory.name} is not a directory")
        files = sorted(
            p for p in directory.iterdir() if p.suffix.lower() in suffixes and p.is_file()
        )
        if not files:
            raise InputError(f"no {'/'.join(suffixes)} files in {directory.name}")
        if len(files) > self._settings.max_documents:
            raise InputError(f"{directory.name} has more than {self._settings.max_documents} files")
        return files

    def load_resumes(self, directory: Path) -> list[Resume]:
        return [self.load_resume(p) for p in self._files(directory, RESUME_SUFFIXES)]

    def load_jobs(self, directory: Path) -> list[Job]:
        return [self.load_job(p) for p in self._files(directory, JOB_SUFFIXES)]

    def match(self, resume: Resume, job: Job, today: date) -> MatchResult:
        result = self._matcher.match(resume, job, today)
        _log.info("matched", extra={"resume": resume.id, "job": job.id, "score": result.score})
        return result

    def rank_candidates(
        self, resumes: list[Resume], job: Job, today: date, top: int | None = None
    ) -> list[Ranked]:
        results = [self._matcher.match(r, job, today) for r in resumes]
        return self._rank([(r.resume_id, "", r) for r in results], top, by="resume")

    def rank_jobs(
        self, resume: Resume, jobs: list[Job], today: date, top: int | None = None
    ) -> list[Ranked]:
        results = [self._matcher.match(resume, j, today) for j in jobs]
        return self._rank([(r.job_id, r.job_title, r) for r in results], top, by="job")

    @staticmethod
    def _rank(
        rows: list[tuple[str, str, MatchResult]], top: int | None, *, by: str
    ) -> list[Ranked]:
        ordered = sorted(rows, key=lambda row: (-row[2].score, len(row[2].missing_musts), row[0]))
        if top is not None:
            ordered = ordered[:top]
        return [
            Ranked(
                rank=i,
                id=ident,
                title=title,
                score=res.score,
                label=res.label,
                missing_musts=res.missing_musts,
            )
            for i, (ident, title, res) in enumerate(ordered, 1)
        ]

    def review(self, resume: Resume, today: date, job: Job | None = None) -> ResumeReview:
        return self._reviewer.review(resume, today, job)

    def audit(self, text: str, resume_id: str, job: Job, today: date) -> InvarianceReport:
        """Score ``text`` and variants that change only personal attributes.

        A passing audit means every variant scored exactly the same as the original.
        """
        resume = self.parse_resume(text, resume_id)
        baseline = self._matcher.match(resume, job, today).score
        cases: list[InvarianceCase] = []
        for change, variant in perturbations(resume, text):
            score = self._matcher.match(self.parse_resume(variant, resume_id), job, today).score
            cases.append(InvarianceCase(change=change, score=score, delta=score - baseline))
        worst = max((abs(c.delta) for c in cases), default=0)
        return InvarianceReport(
            resume_id=resume_id,
            job_id=job.id,
            baseline=baseline,
            cases=cases,
            max_abs_delta=worst,
            ignored_attributes=resume.ignored_attributes,
            passed=worst == 0,
        )

    def summarize(self, result: MatchResult) -> str:
        return self._writer.write(facts_for(result))
