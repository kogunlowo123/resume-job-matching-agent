"""Tests for the service, reporting, summaries, security helpers and the composition root."""

from __future__ import annotations

import csv
import io
import json
from datetime import date
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from jobmatch.container import build_service
from jobmatch.errors import ConfigurationError, InputError, ProviderError
from jobmatch.models import Ranked, Resume
from jobmatch.reporting import (
    render_audit_md,
    render_json,
    render_match_md,
    render_ranking_csv,
    render_ranking_md,
    render_review_md,
    render_skills_md,
)
from jobmatch.security import csv_safe, md_cell, md_code, redact, scrub_pii
from jobmatch.summary import (
    LLMSummaryWriter,
    MatchFacts,
    SummaryWriter,
    TemplateSummaryWriter,
    facts_for,
)
from tests.conftest import SAMPLES, TODAY, job, make_service, make_settings, resume, role

GOOD_RESUME = (
    "Ann Lee\nann@example.org | +1 202 555 0100\n\nSummary\nEngineer.\n\nExperience\n"
    "Senior Engineer, Foo Inc (Jan 2019 - Present)\n- Built Python services on AWS for 2 million users.\n"
    "- Ran Kubernetes clusters.\n\nSkills\nPython, AWS, Kubernetes, Kafka\n\n"
    "Education\nBSc Computer Science, Big University, 2018\n"
)
GOOD_JOB = "Senior Engineer\n\nRequirements\n- Python\n- AWS\n- Kafka\n- 3+ years of experience\n"


class TestSecurity:
    def test_redact(self) -> None:
        assert "hunter22" not in redact("password=hunter22")
        assert "hunter22" not in redact("run --password hunter22 now")
        assert "sk-ant-" not in redact("key sk-ant-abcdefghijklmnopqrstuv")
        assert redact("plain text") == "plain text"
        assert "[REDACTED]" in redact("Authorization: Bearer abcdefghijklmnopqrstu")

    def test_md_helpers(self) -> None:
        assert md_cell("a|b\n<x>") == "a\\|b &lt;x&gt;"
        assert md_code("a`b|c") == "`a'b\\|c`"

    @pytest.mark.parametrize("prefix", ["=", "+", "-", "@"])
    def test_csv_safe(self, prefix: str) -> None:
        assert csv_safe(prefix + "cmd").startswith("'")
        assert csv_safe("fine") == "fine"

    def test_scrub_pii(self) -> None:
        text = "Mail bob@example.org or +1 (202) 555-0100, see https://linkedin.com/in/bob"
        cleaned = scrub_pii(text)
        assert "bob@" not in cleaned and "555" not in cleaned and "linkedin" not in cleaned
        assert "[email]" in cleaned and "[phone]" in cleaned and "[link]" in cleaned
        assert scrub_pii("Grew revenue 2019 to 2021 by 45%") == "Grew revenue 2019 to 2021 by 45%"


class TestService:
    def test_load_and_match_samples(self) -> None:
        svc = make_service()
        r = svc.load_resume(SAMPLES / "resumes" / "avery-backend.md")
        j = svc.load_job(SAMPLES / "jobs" / "senior-backend-engineer.md")
        result = svc.match(r, j, TODAY)
        assert result.score >= 85 and result.label == "strong" and not result.missing_musts

    def test_structured_job_file(self) -> None:
        svc = make_service()
        j = svc.load_job(SAMPLES / "jobs" / "ml-engineer.json")
        assert j.title == "Machine Learning Engineer" and j.seniority == 3

    def test_rank_candidates_orders_and_limits(self) -> None:
        svc = make_service()
        resumes = svc.load_resumes(SAMPLES / "resumes")
        j = svc.load_job(SAMPLES / "jobs" / "senior-backend-engineer.md")
        ranked = svc.rank_candidates(resumes, j, TODAY)
        assert ranked[0].id == "avery-backend" and ranked[0].rank == 1
        assert [r.score for r in ranked] == sorted((r.score for r in ranked), reverse=True)
        assert len(svc.rank_candidates(resumes, j, TODAY, top=2)) == 2
        assert all(r.title == "" for r in ranked)

    def test_rank_jobs(self) -> None:
        svc = make_service()
        r = svc.load_resume(SAMPLES / "resumes" / "casey-data.md")
        ranked = svc.rank_jobs(r, svc.load_jobs(SAMPLES / "jobs"), TODAY)
        assert ranked[0].id == "ml-engineer" and ranked[0].title == "Machine Learning Engineer"

    def test_ties_break_on_fewer_missing_then_id(self) -> None:
        svc = make_service()
        same = "Sam\nsam@example.org\n\nExperience\nDev (2020 - 2024)\n- Built Python\n"
        resumes = [svc.parse_resume(same, "b"), svc.parse_resume(same, "a")]
        ranked = svc.rank_candidates(resumes, svc.parse_job(GOOD_JOB, "j"), TODAY)
        assert [r.id for r in ranked] == ["a", "b"]

    def test_review_delegates(self) -> None:
        svc = make_service()
        rev = svc.review(svc.parse_resume(GOOD_RESUME, "x"), TODAY)
        assert "python" in rev.skills_found

    def test_audit_passes_and_covers_cases(self) -> None:
        svc = make_service()
        report = svc.audit(GOOD_RESUME, "ann", svc.parse_job(GOOD_JOB, "j"), TODAY)
        assert report.passed and report.max_abs_delta == 0
        assert len(report.cases) >= 5 and all(c.delta == 0 for c in report.cases)

    def test_audit_detects_a_leak(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """If scoring ever read a personal field, the audit must fail."""
        import jobmatch.matching as matching

        real = matching.scoring_view

        def leaky(r: Resume) -> Resume:
            view = real(r)
            view.education_level = 4 if r.name == "Ann Lee" else 0
            return view

        monkeypatch.setattr(matching, "scoring_view", leaky)
        svc = make_service()
        report = svc.audit(GOOD_RESUME, "ann", job(["python"], education_level=2), TODAY)
        assert not report.passed and report.max_abs_delta > 0

    def test_summarize_uses_writer(self) -> None:
        svc = make_service()
        result = svc.match(svc.parse_resume(GOOD_RESUME, "x"), svc.parse_job(GOOD_JOB, "j"), TODAY)
        text = svc.summarize(result)
        assert "of 100" in text and "Senior Engineer" in text

    @pytest.mark.parametrize("name", ["cv.pdf", "cv.docx"])
    def test_rejects_unsupported_resume(self, tmp_path: Path, name: str) -> None:
        path = tmp_path / name
        path.write_text("x", encoding="utf-8")
        with pytest.raises(InputError, match=r"\.txt or \.md"):
            make_service().load_resume(path)

    def test_rejects_unsupported_job(self, tmp_path: Path) -> None:
        path = tmp_path / "job.pdf"
        path.write_text("x", encoding="utf-8")
        with pytest.raises(InputError, match="jobs must be"):
            make_service().load_job(path)

    def test_missing_oversized_and_binary_files(self, tmp_path: Path) -> None:
        svc = make_service(max_document_bytes=1000)
        with pytest.raises(InputError, match="cannot read"):
            svc.load_resume(tmp_path / "nope.txt")
        big = tmp_path / "big.txt"
        big.write_text("x" * 2000, encoding="utf-8")
        with pytest.raises(InputError, match="larger"):
            svc.load_resume(big)
        binary = tmp_path / "bin.txt"
        binary.write_bytes(b"\xff\xfe\x00\x81")
        with pytest.raises(InputError, match="UTF-8"):
            svc.load_resume(binary)

    def test_bom_is_tolerated(self, tmp_path: Path) -> None:
        path = tmp_path / "bom.txt"
        path.write_bytes(b"\xef\xbb\xbfAnn\nSkills: Python\n")
        assert make_service().load_resume(path).name == "Ann"

    def test_directory_problems(self, tmp_path: Path) -> None:
        svc = make_service(max_documents=1)
        with pytest.raises(InputError, match="not a directory"):
            svc.load_resumes(tmp_path / "nope")
        with pytest.raises(InputError, match=r"no \.txt"):
            svc.load_resumes(tmp_path)
        (tmp_path / "a.txt").write_text("A\n", encoding="utf-8")
        (tmp_path / "b.md").write_text("B\n", encoding="utf-8")
        with pytest.raises(InputError, match="more than 1"):
            svc.load_resumes(tmp_path)

    def test_custom_taxonomy_and_policy_files(self, tmp_path: Path) -> None:
        tax = tmp_path / "t.yaml"
        tax.write_text("- {id: cobol, name: COBOL, category: language}\n", encoding="utf-8")
        pol = tmp_path / "p.yaml"
        pol.write_text("listed_strength: 0.9\n", encoding="utf-8")
        svc = make_service(taxonomy_file=tax, policy_file=pol)
        assert svc.taxonomy.resolve("COBOL") == "cobol"
        r = resume(skills_listed=["cobol"])
        res = svc.match(r, job(["cobol"]), TODAY)
        assert res.requirements[0].strength == 0.9


class TestReporting:
    def _result(self):  # type: ignore[no-untyped-def]
        svc = make_service()
        text = GOOD_RESUME + "\nDate of birth: 1980-01-01\n"
        return svc.match(svc.parse_resume(text, "ann|x"), svc.parse_job(GOOD_JOB, "j"), TODAY)

    def test_match_markdown(self) -> None:
        md = render_match_md(self._result(), "A summary.")
        for part in (
            "# Match:",
            "## Breakdown",
            "## Requirements",
            "## Strengths",
            "A summary.",
            "Ignored attributes",
        ):
            assert part in md
        assert "ann\\|x" in md and "date of birth" in md

    def test_match_markdown_gaps(self) -> None:
        svc = make_service()
        res = svc.match(
            svc.parse_resume("Bob\nSkills: Python\n", "b"), svc.parse_job(GOOD_JOB, "j"), TODAY
        )
        assert "## Gaps" in render_match_md(res)

    def test_hostile_content_is_escaped(self) -> None:
        svc = make_service()
        text = "Eve\n\nExperience\nDev at X (2020 - 2024)\n- Built Python | <script>alert(1)</script> `x`\n"
        res = svc.match(svc.parse_resume(text, "eve"), svc.parse_job(GOOD_JOB, "j"), TODAY)
        md = render_match_md(res)
        assert "<script>" not in md and "`x`" not in md

    def test_json_roundtrip(self) -> None:
        data = json.loads(render_json(self._result()))
        assert data["job_id"] == "j" and data["score"] > 0
        rows = [Ranked(rank=1, id="a", title="", score=5, label="weak", missing_musts=[])]
        assert json.loads(render_json(rows))[0]["id"] == "a"

    def test_ranking_outputs(self) -> None:
        rows = [
            Ranked(rank=1, id="=evil", title="Job|One", score=90, label="strong", missing_musts=[]),
            Ranked(rank=2, id="b", title="", score=10, label="weak", missing_musts=["Go", "Rust"]),
        ]
        md = render_ranking_md(rows, "Title")
        assert "Job\\|One (=evil)" in md and "Go, Rust" in md and "| 2 | b |" in md
        parsed = list(csv.reader(io.StringIO(render_ranking_csv(rows))))
        assert parsed[0][0] == "rank" and parsed[1][1] == "'=evil" and parsed[2][5] == "Go; Rust"

    def test_review_markdown(self) -> None:
        svc = make_service()
        rev = svc.review(
            svc.parse_resume("Ann\n\nnothing", "a"), TODAY, svc.parse_job(GOOD_JOB, "j")
        )
        md = render_review_md(rev)
        assert (
            "**issue**" in md
            and "Keywords in the job" in md
            and "only if you have really used it" in md
        )
        clean = svc.review(svc.parse_resume(GOOD_RESUME, "a"), TODAY)
        clean.findings.clear()
        assert "No findings." in render_review_md(clean)

    def test_audit_markdown(self) -> None:
        svc = make_service()
        report = svc.audit(
            GOOD_RESUME + "Marital status: single\n", "ann", svc.parse_job(GOOD_JOB, "j"), TODAY
        )
        md = render_audit_md(report)
        assert "**PASS.**" in md and "+0" in md and "marital status" in md
        failed = report.model_copy(update={"passed": False, "max_abs_delta": 3})
        assert "**FAIL.**" in render_audit_md(failed)

    def test_skills_table(self) -> None:
        svc = make_service()
        md = render_skills_md(svc.taxonomy.search("kube"))
        assert "Kubernetes" in md and "k8s" in md


def _facts(**over: object) -> MatchFacts:
    data: dict[str, object] = {
        "job_title": "Engineer",
        "score": 80,
        "label": "strong",
        "must_total": 4,
        "must_met": 3,
        "nice_total": 2,
        "nice_met": 1,
        "years_shown": 6.5,
        "missing_musts": ["Rust"],
        "strengths": ["Python"],
    }
    data.update(over)
    return MatchFacts.model_validate(data)


class FakeLLM:
    def __init__(self, reply: str | Exception) -> None:
        self.reply = reply
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


class TestSummary:
    def test_template(self) -> None:
        text = TemplateSummaryWriter().write(_facts())
        assert "80 of 100" in text and "3 of 4" in text and "Not shown: Rust." in text
        bare = TemplateSummaryWriter().write(_facts(strengths=[], missing_musts=[]))
        assert "Shown in role" not in bare and "Not shown" not in bare

    def test_facts_for_result(self) -> None:
        svc = make_service()
        res = svc.match(svc.parse_resume(GOOD_RESUME, "x"), svc.parse_job(GOOD_JOB, "j"), TODAY)
        facts = facts_for(res)
        assert facts.must_total == 3 and facts.must_met == 3 and facts.score == res.score
        assert "Ann" not in facts.model_dump_json() and "example.org" not in facts.model_dump_json()

    def test_llm_accepted_when_grounded(self) -> None:
        llm = FakeLLM("Strong fit at 80 of 100 with 3 of 4 required skills.")
        assert LLMSummaryWriter(llm).write(_facts()).startswith("Strong fit")
        assert "Ann" not in llm.calls[0][1]

    @pytest.mark.parametrize(
        "reply",
        [
            "They scored 93 which is great.",
            "",
            "x" * 1000,
        ],
    )
    def test_llm_rejected_falls_back(self, reply: str) -> None:
        assert "of 100" in LLMSummaryWriter(FakeLLM(reply)).write(_facts())

    def test_llm_error_falls_back(self) -> None:
        assert "of 100" in LLMSummaryWriter(FakeLLM(ProviderError("down"))).write(_facts())

    def test_protocol(self) -> None:
        assert isinstance(TemplateSummaryWriter(), SummaryWriter)


class TestContainer:
    def test_default_is_template(self) -> None:
        assert build_service(make_settings())._writer.__class__ is TemplateSummaryWriter

    def test_openai_and_anthropic_wiring(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            if "anthropic" in str(request.url):
                return httpx.Response(
                    200, json={"content": [{"type": "text", "text": "Fit is 80 of 100."}]}
                )
            return httpx.Response(
                200, json={"choices": [{"message": {"content": "Fit is 80 of 100."}}]}
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        for provider, key in (("openai", "openai_api_key"), ("anthropic", "anthropic_api_key")):
            settings = make_settings(llm_provider=provider, **{key: SecretStr("k" * 30)})
            svc = build_service(settings, http_client=client)
            res = svc.match(svc.parse_resume(GOOD_RESUME, "x"), svc.parse_job(GOOD_JOB, "j"), TODAY)
            assert svc.summarize(res)
        assert len(seen) == 2

    @pytest.mark.parametrize("provider", ["openai", "anthropic"])
    def test_missing_keys(self, provider: str, monkeypatch: pytest.MonkeyPatch) -> None:
        for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
            monkeypatch.delenv(name, raising=False)
        with pytest.raises(ConfigurationError, match="must be set"):
            build_service(make_settings(llm_provider=provider))

    def test_injected_writer_wins(self) -> None:
        writer = TemplateSummaryWriter()
        assert build_service(make_settings(), summary_writer=writer)._writer is writer

    def test_default_date_type(self) -> None:
        assert isinstance(TODAY, date)
        assert role().present is True
