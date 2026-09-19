"""End-to-end tests: the sample resumes and jobs through the CLI."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from jobmatch.cli import main
from tests.conftest import SAMPLES

pytestmark = pytest.mark.integration

RESUMES = SAMPLES / "resumes"
JOBS = SAMPLES / "jobs"
BACKEND = JOBS / "senior-backend-engineer.md"
AS_OF = ["--as-of", "2026-09-19"]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in list(os.environ):
        if name.startswith("JOBMATCH_") or name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)


def run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str, str]:
    code = main([*AS_OF, *args])
    out = capsys.readouterr()
    return code, out.out, out.err


class TestMatchCommand:
    def test_markdown(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, out, _ = run(capsys, "match", str(RESUMES / "avery-backend.md"), str(BACKEND))
        assert code == 0 and "strong match" in out and "## Requirements" in out

    def test_json_and_output_file(self, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
        target = tmp_path / "out.json"
        code, out, err = run(
            capsys,
            "match",
            str(RESUMES / "avery-backend.md"),
            str(BACKEND),
            "--format",
            "json",
            "--output",
            str(target),
        )
        assert code == 0 and out == "" and "wrote out.json" in err
        assert json.loads(target.read_text(encoding="utf-8"))["label"] == "strong"

    def test_summary_flag(self, capsys: pytest.CaptureFixture[str]) -> None:
        _, out, _ = run(
            capsys, "match", str(RESUMES / "avery-backend.md"), str(BACKEND), "--summary"
        )
        assert "of 100 for Senior Backend Engineer" in out

    def test_min_score_gate(self, capsys: pytest.CaptureFixture[str]) -> None:
        args = ("match", str(RESUMES / "blake-junior.md"), str(BACKEND))
        assert run(capsys, *args, "--min-score", "50")[0] == 1
        assert run(capsys, *args, "--min-score", "1")[0] == 0

    def test_csv_is_rankings_only(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, _, err = run(
            capsys, "match", str(RESUMES / "avery-backend.md"), str(BACKEND), "--format", "csv"
        )
        assert code == 2 and "rankings only" in err

    def test_hides_personal_details_in_parse(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, out, _ = run(capsys, "parse", "resume", str(RESUMES / "avery-backend.md"))
        assert code == 0 and "avery.quinn@" not in out and "555" not in out
        assert json.loads(out)["roles"][0]["title"] == "Senior Software Engineer"


class TestRankings:
    def test_rank_candidates(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, out, _ = run(capsys, "rank-candidates", str(RESUMES), str(BACKEND), "--top", "3")
        rows = [ln for ln in out.splitlines() if ln.startswith("| ") and "---" not in ln][1:]
        assert code == 0 and len(rows) == 3 and "avery-backend" in rows[0]

    def test_rank_candidates_csv(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, out, _ = run(capsys, "rank-candidates", str(RESUMES), str(BACKEND), "--format", "csv")
        lines = out.strip().splitlines()
        assert code == 0 and lines[0].startswith("rank,id") and len(lines) == 6

    def test_rank_jobs_json(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, out, _ = run(
            capsys, "rank-jobs", str(RESUMES / "emery-frontend.md"), str(JOBS), "--format", "json"
        )
        data = json.loads(out)
        assert code == 0 and data[0]["id"] == "frontend-engineer"

    def test_sample_expectations(self, capsys: pytest.CaptureFixture[str]) -> None:
        _, out, _ = run(capsys, "rank-candidates", str(RESUMES), str(BACKEND), "--format", "json")
        ranked = {r["id"]: r for r in json.loads(out)}
        assert ranked["avery-backend"]["label"] == "strong"
        assert ranked["devon-keywords"]["label"] == "weak"
        assert ranked["devon-keywords"]["score"] < ranked["casey-data"]["score"]


class TestAnalyzeAuditSkills:
    def test_analyze(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, out, _ = run(
            capsys, "analyze", str(RESUMES / "devon-keywords.txt"), "--job", str(BACKEND)
        )
        assert code == 0 and "listed but never used" in out and "Keywords in the job" in out

    def test_analyze_json(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, out, _ = run(capsys, "analyze", str(RESUMES / "avery-backend.md"), "--format", "json")
        assert code == 0 and json.loads(out)["resume_id"] == "avery-backend"

    def test_audit_passes_every_sample(self, capsys: pytest.CaptureFixture[str]) -> None:
        for resume in sorted(RESUMES.iterdir()):
            code, out, _ = run(capsys, "audit", str(resume), str(BACKEND))
            assert code == 0 and "**PASS.**" in out, resume.name

    def test_audit_rejects_pdf(self, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
        pdf = tmp_path / "cv.pdf"
        pdf.write_text("x", encoding="utf-8")
        code, _, err = run(capsys, "audit", str(pdf), str(BACKEND))
        assert code == 2 and ".txt or .md" in err

    def test_skills(self, capsys: pytest.CaptureFixture[str]) -> None:
        code, out, _ = run(capsys, "skills", "kube")
        assert code == 0 and "Kubernetes" in out and "Terraform" not in out
        code, out, _ = run(capsys, "skills")
        assert code == 0 and out.count("\n") > 80


class TestErrors:
    def test_missing_file(self, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
        code, _, err = run(capsys, "match", str(tmp_path / "nope.md"), str(BACKEND))
        assert code == 2 and err.startswith("error:")

    def test_invalid_settings(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("JOBMATCH_RETRY_ATTEMPTS", "0")
        code, _, err = run(capsys, "skills")
        assert code == 2 and "error:" in err

    def test_bad_date_is_argparse_error(self) -> None:
        with pytest.raises(SystemExit) as exc:
            main(["--as-of", "yesterday", "skills"])
        assert exc.value.code == 2

    def test_secrets_are_redacted_in_errors(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        bad = tmp_path / "job.json"
        bad.write_text('{"title": "T", "must": ["password=hunter2222"]}', encoding="utf-8")
        code, _, err = run(capsys, "match", str(RESUMES / "avery-backend.md"), str(bad))
        assert code == 2 and "hunter2222" not in err

    def test_hostile_resume_does_not_break_output(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        cv = tmp_path / "evil.md"
        cv.write_text(
            "=cmd|' /C calc'!A0\n\nExperience\nDev (2020 - 2024)\n- Python <img src=x onerror=alert(1)>\n",
            encoding="utf-8",
        )
        code, out, _ = run(capsys, "match", str(cv), str(BACKEND))
        assert code == 0 and "<img" not in out
