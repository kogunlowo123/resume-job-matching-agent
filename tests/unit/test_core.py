"""Tests for dates, taxonomy, parsers, extraction, scoring, fairness and review."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from jobmatch.analysis import ResumeReviewer
from jobmatch.config import Policy, load_policy
from jobmatch.dates import month_index, parse_point, parse_range
from jobmatch.errors import ConfigurationError, InputError
from jobmatch.extract import SkillExtractor, merged_months, role_interval, total_years
from jobmatch.fairness import perturbations, scoring_view
from jobmatch.matching import Matcher, resume_seniority
from jobmatch.models import Job, MatchResult, Resume
from jobmatch.parsers import (
    JobParser,
    ResumeParser,
    detect_ignored_attributes,
    education_level,
    job_from_data,
    title_seniority,
    years_seniority,
)
from jobmatch.taxonomy import Taxonomy, load_taxonomy
from tests.conftest import TODAY, job, make_settings, resume, role

TAX = Taxonomy()


class TestDates:
    @pytest.mark.parametrize(
        ("text", "end", "expected"),
        [
            ("Jan 2020", False, date(2020, 1, 1)),
            ("January 2020", False, date(2020, 1, 1)),
            ("Sept. 2019", False, date(2019, 9, 1)),
            ("03/2018", False, date(2018, 3, 1)),
            ("2020", False, date(2020, 1, 1)),
            ("2020", True, date(2020, 12, 1)),
            ("13/2018", False, None),
            ("Foo 2020", False, None),
            ("last week", False, None),
        ],
    )
    def test_parse_point(self, text: str, end: bool, expected: date | None) -> None:
        assert parse_point(text, end=end) == expected

    def test_range_present_and_remainder(self) -> None:
        parsed = parse_range("Engineer, Acme (Jan 2020 - Present)")
        assert parsed is not None
        start, end, present, rest = parsed
        assert (start, end, present) == (date(2020, 1, 1), None, True)
        assert "Engineer" in rest

    def test_range_words_and_years(self) -> None:
        parsed = parse_range("Dev at X 2015 to 2018")
        assert parsed is not None and parsed[1] == date(2018, 12, 1)

    def test_no_range(self) -> None:
        assert parse_range("Just some text") is None

    def test_bad_start_month_rejected(self) -> None:
        assert parse_range("Foo 2019 - 2020") is None

    def test_bad_end_rejected(self) -> None:
        assert parse_range("Dev 2019 - Foo 2020") is None

    def test_month_index(self) -> None:
        assert month_index(date(2020, 3, 1)) == 2020 * 12 + 2


class TestTaxonomy:
    def test_mentions_use_aliases(self) -> None:
        found = TAX.mentions("Ran k8s on EKS, wrote Node.js and C++ services")
        assert {"kubernetes", "nodejs", "cpp"} <= set(found)

    def test_go_is_case_sensitive(self) -> None:
        assert "go" in TAX.mentions("Services in Go and Python")
        assert "go" not in TAX.mentions("Good to go, will go far")

    def test_negation_ignored(self) -> None:
        assert "java" not in TAX.mentions("I have no experience with Java")
        assert "java" not in TAX.mentions("Without Java or Kotlin")
        assert "java" in TAX.mentions("Java. No regrets")

    def test_no_partial_word_hits(self) -> None:
        assert "java" not in TAX.mentions("JavaScript only")
        assert "javascript" in TAX.mentions("JavaScript only")

    def test_resolve_and_search(self) -> None:
        assert TAX.resolve("K8s") == "kubernetes"
        assert TAX.resolve("nothing at all") is None
        assert TAX.search("postgres")[0].id == "postgresql"
        assert len(TAX) > 80 and len(list(TAX)) == len(TAX)
        assert TAX.get("python") is not None and TAX.get("ghost") is None

    def test_references_are_consistent(self) -> None:
        ids = {s.id for s in TAX}
        for skill in TAX:
            assert set(skill.implies) <= ids
            assert set(skill.related) <= ids

    def test_load_extends_and_replaces(self, tmp_path: Path) -> None:
        path = tmp_path / "t.yaml"
        path.write_text(
            "skills:\n"
            "  - {id: cobol, name: COBOL, category: language, aliases: [cobol85]}\n"
            "  - {id: python, name: Python, category: language, aliases: [py3k]}\n",
            encoding="utf-8",
        )
        tax = load_taxonomy(path)
        assert tax.resolve("cobol85") == "cobol"
        assert tax.resolve("py3k") == "python"
        assert tax.resolve("python3") is None

    def test_load_none_is_builtin(self) -> None:
        assert len(load_taxonomy(None)) == len(TAX)

    @pytest.mark.parametrize(
        "body",
        [
            "skills: 3",
            "- {id: BAD ID, name: x, category: y}",
            "- {id: a, name: x, category: y, implies: [ghost]}",
            "- {id: a, name: x, category: y, surprise: 1}",
            "[unclosed",
        ],
    )
    def test_load_rejects_bad_files(self, tmp_path: Path, body: str) -> None:
        path = tmp_path / "t.yaml"
        path.write_text(body, encoding="utf-8")
        with pytest.raises(ConfigurationError):
            load_taxonomy(path)

    def test_load_missing_and_huge(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigurationError):
            load_taxonomy(tmp_path / "nope.yaml")
        big = tmp_path / "big.yaml"
        big.write_bytes(b"#" * 2_000_001)
        with pytest.raises(ConfigurationError, match="larger"):
            load_taxonomy(big)

    def test_duplicate_ids_rejected(self) -> None:
        skills = tuple(TAX)[:1] * 2
        with pytest.raises(ConfigurationError, match="unique"):
            Taxonomy(skills)


RESUME_TEXT = """# Jane Roe
jane@example.org | +1 202 555 0111

## Summary
Backend engineer.

## Work Experience
### Senior Engineer, Acme Corp (Jan 2019 - Present)
- Built Python services on AWS serving 2 million users.
- Led a team of 4.

Data Analyst at Beta LLC, Jun 2015 - Dec 2018
- Wrote SQL reports.

## Skills
Languages: Python, Go
Tools: Docker, Kubernetes

## Education
MSc Computer Science, Some University, 2015

## Certifications
- AWS Certified Developer
"""


class TestResumeParser:
    def test_sections_and_fields(self) -> None:
        r = ResumeParser(TAX).parse(RESUME_TEXT, "jane")
        assert r.name == "Jane Roe"
        assert r.email == "jane@example.org"
        assert r.phone.startswith("+1")
        assert r.summary == "Backend engineer."
        assert [x.title for x in r.roles] == ["Senior Engineer", "Data Analyst"]
        assert r.roles[0].company == "Acme Corp" and r.roles[0].present
        assert r.roles[1].start == date(2015, 6, 1) and r.roles[1].end == date(2018, 12, 1)
        assert len(r.roles[0].bullets) == 2
        assert {"python", "go", "docker", "kubernetes"} <= set(r.skills_listed)
        assert r.education_level == 3
        assert r.certifications == ["AWS Certified Developer"]
        assert {"summary", "experience", "skills", "education", "certifications"} <= set(r.sections)
        assert r.word_count > 30

    def test_inline_headings_and_windows_newlines(self) -> None:
        text = "Sam\r\nSkills: Python, SQL\r\nSummary: Analyst\r\n"
        r = ResumeParser(TAX).parse(text, "sam")
        assert set(r.skills_listed) == {"python", "sql"}
        assert r.summary == "Analyst"

    def test_experience_without_dates(self) -> None:
        text = "Ann\n\nExperience\nEngineer at Foo\n- Did Python things\n"
        r = ResumeParser(TAX).parse(text, "ann")
        assert r.roles[0].start is None
        assert r.roles[0].bullets == ["Did Python things"]

    def test_company_on_next_line(self) -> None:
        text = "Ann\n\nExperience\nEngineer (2020 - 2022)\nFoo Inc\n- Built stuff\n"
        r = ResumeParser(TAX).parse(text, "ann")
        assert r.roles[0].company == "Foo Inc"
        assert r.roles[0].bullets == ["Built stuff"]

    def test_empty_text(self) -> None:
        r = ResumeParser(TAX).parse("", "empty")
        assert r.roles == [] and r.name == "" and r.word_count == 0

    def test_ignored_attributes(self) -> None:
        text = "DOB: 1990-01-01\nMarital status: single\nAge: 34\nPhoto attached"
        found = detect_ignored_attributes(text)
        assert {"date of birth", "marital status", "age", "photo"} <= set(found)
        assert detect_ignored_attributes("Built things in Python") == []

    @pytest.mark.parametrize(
        ("text", "level"),
        [
            ("PhD in physics", 4),
            ("MBA, MSc", 3),
            ("B.Sc. Biology", 2),
            ("Bachelor of Arts", 2),
            ("Diploma in design", 1),
            ("High school", 0),
            ("BSc and MSc", 3),
        ],
    )
    def test_education_level(self, text: str, level: int) -> None:
        assert education_level(text) == level

    def test_seniority_helpers(self) -> None:
        assert title_seniority("Senior Developer") == 3
        assert title_seniority("Junior Analyst") == 1
        assert title_seniority("Staff Engineer") == 4
        assert title_seniority("Developer") is None
        assert [years_seniority(y) for y in (0, 3, 6, 12)] == [1, 2, 3, 4]


JOB_TEXT = """Senior Data Engineer at Contoso

## About the role
Python is used across the team.

## Requirements
- 5+ years of experience building data platforms
- Python and SQL
- 3 years of Spark
- Bachelor's degree or equivalent experience

## Nice to have
- Airflow
- Kafka experience is a plus
"""


class TestJobParser:
    def test_text_job(self) -> None:
        j = JobParser(TAX).parse(JOB_TEXT, "de")
        priorities = {r.skill: r.priority for r in j.requirements}
        assert j.title == "Senior Data Engineer" and j.company == "Contoso"
        assert priorities["python"] == "must" and priorities["sql"] == "must"
        assert priorities["airflow"] == "nice" and priorities["kafka"] == "nice"
        assert {r.skill: r.min_years for r in j.requirements}["spark"] == 3.0
        assert j.min_years == 5.0 and j.seniority == 3
        assert j.education_level is None

    def test_context_and_labelled_title(self) -> None:
        text = (
            "Title: Analyst\nCompany: Foo\n\nResponsibilities\n- Use Excel daily\n\n"
            "Requirements\n- SQL\n"
        )
        j = JobParser(TAX).parse(text, "a")
        assert j.title == "Analyst" and j.company == "Foo"
        priorities = {r.skill: r.priority for r in j.requirements}
        assert priorities == {"excel": "context", "sql": "must"}

    def test_must_outranks_context(self) -> None:
        text = "Analyst\n\nResponsibilities\n- Write SQL\n\nRequirements\n- SQL\n"
        j = JobParser(TAX).parse(text, "a")
        assert [(r.skill, r.priority) for r in j.requirements] == [("sql", "must")]

    def test_degree_levels(self) -> None:
        j = JobParser(TAX).parse("Dev\n\nRequirements\n- Master's degree in CS\n", "d")
        assert j.education_level == 3
        j = JobParser(TAX).parse("Dev\n\nRequirements\n- Bachelor's or Master's degree\n", "d")
        assert j.education_level == 2

    def test_seniority_from_years(self) -> None:
        j = JobParser(TAX).parse("Developer\n\nRequirements\n- 6 years of experience\n", "d")
        assert j.seniority == 3

    def test_empty_job_rejected(self) -> None:
        with pytest.raises(InputError):
            JobParser(TAX).parse("   \n", "empty")

    def test_structured_job(self) -> None:
        text = (
            '{"id": "x1", "title": "ML Engineer", "must": ["python", '
            '{"skill": "ml", "years": 3}, "sql:2"], "nice": ["k8s", "python"], '
            '"min_years": 4, "seniority": "senior", "education": "master"}'
        )
        j = job_from_data(text, "fallback", TAX)
        assert j.id == "x1" and j.seniority == 3 and j.education_level == 3
        reqs = {r.skill: (r.priority, r.min_years) for r in j.requirements}
        assert reqs == {
            "python": ("must", None),
            "ml": ("must", 3.0),
            "sql": ("must", 2.0),
            "kubernetes": ("nice", None),
        }

    def test_structured_yaml(self) -> None:
        j = job_from_data("title: Dev\nmust: [python]\nseniority: 2\n", "y", TAX)
        assert j.seniority == 2 and j.requirements[0].skill == "python"

    @pytest.mark.parametrize(
        "body",
        [
            "{not json",
            "[1, 2]",
            '{"title": ""}',
            '{"title": "T", "must": "python"}',
            '{"title": "T", "must": ["nonexistentskill"]}',
            '{"title": "T", "must": [5]}',
            '{"title": "T", "must": ["python:abc"]}',
            '{"title": "T", "must": [{"skill": "python", "years": -1}]}',
            '{"title": "T", "seniority": "wizard"}',
            '{"title": "T", "education": "wizard"}',
            '{"title": "T", "min_years": "many"}',
            '{"title": "T", "min_years": true}',
            '{"title": "T", "seniority": 9}',
        ],
    )
    def test_structured_rejects(self, body: str) -> None:
        with pytest.raises(InputError):
            job_from_data(body, "j", TAX)


class TestExtraction:
    def test_merged_months(self) -> None:
        assert merged_months([(0, 11), (6, 17), (30, 35)]) == 18 + 6
        assert merged_months([]) == 0

    def test_role_interval(self) -> None:
        assert role_interval(role(start=None), TODAY) is None
        assert role_interval(role(start=date(2030, 1, 1)), TODAY) is None
        interval = role_interval(role(start=date(2026, 1, 1), end=date(2026, 3, 1)), TODAY)
        assert interval == (month_index(date(2026, 1, 1)), month_index(date(2026, 3, 1)))

    def test_demonstrated_years_merge_overlap(self) -> None:
        r = resume(
            [
                role("Dev", date(2020, 1, 1), date(2021, 12, 1), ["Built Python APIs"]),
                role("Dev", date(2021, 1, 1), date(2022, 12, 1), ["More Python work"]),
            ]
        )
        ev = SkillExtractor(TAX).extract(r, TODAY)["python"]
        assert ev.years == 3.0 and ev.last_used == date(2022, 12, 1)
        assert len(ev.demonstrated) == 2
        assert total_years(r, TODAY) == 3.0

    def test_listed_versus_demonstrated(self) -> None:
        r = resume(
            [role("Dev", bullets=["Used Docker daily"])],
            skills_listed=["docker", "go"],
            summary="Loves Rust",
            certifications=["Terraform Associate"],
        )
        ev = SkillExtractor(TAX).extract(r, TODAY)
        assert ev["docker"].demonstrated and ev["docker"].listed
        assert ev["go"].listed and not ev["go"].demonstrated
        assert ev["rust"].listed and ev["terraform"].listed

    def test_quotes_are_scrubbed_and_capped(self) -> None:
        bullet = "Emailed bob@example.org about Python " + "x" * 300
        r = resume([role("Dev", bullets=[bullet])])
        quote = SkillExtractor(TAX).extract(r, TODAY)["python"].demonstrated[0]
        assert "bob@" not in quote and "[email]" in quote and len(quote) <= 160

    def test_quote_count_is_capped(self) -> None:
        r = resume([role("Dev", bullets=[f"Wrote Python module {i}" for i in range(6)])])
        assert len(SkillExtractor(TAX).extract(r, TODAY)["python"].demonstrated) == 3

    def test_implied_and_related(self) -> None:
        r = resume([role("Dev", bullets=["Ran Kubernetes clusters", "Tuned PostgreSQL"])])
        extractor = SkillExtractor(TAX)
        adjacent, related = extractor.implied(extractor.extract(r, TODAY))
        assert adjacent["docker"].via == "kubernetes" and "containers" in adjacent
        assert adjacent["sql"].via == "postgresql"
        assert related["mysql"].via == "postgresql"
        assert "postgresql" not in related

    def test_stronger_source_wins(self) -> None:
        r = resume(
            [
                role("Dev", date(2015, 1, 1), date(2016, 1, 1), ["Ran Docker"]),
                role("Dev", date(2017, 1, 1), date(2022, 1, 1), ["Ran Kubernetes"]),
            ]
        )
        extractor = SkillExtractor(TAX)
        adjacent, _ = extractor.implied(extractor.extract(r, TODAY))
        assert adjacent["containers"].via == "kubernetes"

    def test_relevant_years(self) -> None:
        r = resume(
            [
                role("Cook", date(2010, 1, 1), date(2014, 12, 1), ["Made food"]),
                role("Dev", date(2015, 1, 1), date(2016, 12, 1), ["Built Kubernetes tooling"]),
                role("Dev", None, None, ["Built Kubernetes tooling"]),
            ]
        )
        assert SkillExtractor(TAX).relevant_years(r, {"containers"}, TODAY) == 2.0
        assert SkillExtractor(TAX).relevant_years(r, {"rust"}, TODAY) == 0.0


def _match(r: Resume, j: Job, policy: Policy | None = None) -> MatchResult:
    return Matcher(TAX, policy).match(r, j, TODAY)


class TestMatching:
    def test_demonstrated_beats_listed_beats_missing(self) -> None:
        r = resume([role("Dev", bullets=["Built Python services"])], skills_listed=["docker"])
        res = _match(r, job(["python", "docker", "rust"]))
        by = {x.skill: x for x in res.requirements}
        assert by["python"].status == "demonstrated" and by["python"].strength == 1.0
        assert by["docker"].status == "listed" and by["docker"].strength == 0.4
        assert by["rust"].status == "missing"
        assert res.missing_musts == ["Rust"]
        assert res.label != "strong"
        assert any("listed but not shown" in g for g in res.gaps)

    def test_listed_skill_is_upgraded_when_a_shown_skill_implies_it(self) -> None:
        r = resume([role("Dev", bullets=["Built a Flask service"])], skills_listed=["python"])
        row = _match(r, job(["python"])).requirements[0]
        assert row.status == "adjacent" and row.note == "via Flask" and row.strength == 0.8

    def test_listed_strength_is_policy(self) -> None:
        r = resume(skills_listed=["docker"])
        low = _match(r, job(["docker"]), Policy(listed_strength=0.2)).score
        high = _match(r, job(["docker"]), Policy(listed_strength=0.9)).score
        assert low < high

    def test_implied_counts_as_met_related_does_not(self) -> None:
        r = resume([role("Dev", bullets=["Ran Kubernetes", "Tuned PostgreSQL"])])
        res = _match(r, job(["containers", "mysql"]))
        by = {x.skill: x for x in res.requirements}
        assert by["containers"].status == "adjacent" and by["containers"].note == "via Kubernetes"
        assert by["mysql"].status == "related"
        assert res.missing_musts == ["MySQL"]
        assert any("related experience" in g for g in res.gaps)

    def test_related_and_implied_factors_are_policy(self) -> None:
        r = resume([role("Dev", bullets=["Ran Kubernetes", "Tuned PostgreSQL"])])
        j = job(["containers", "mysql"])
        assert (
            _match(r, j, Policy(implied_factor=0.4, related_factor=0.1)).score < _match(r, j).score
        )

    def test_recency_factors(self) -> None:
        old = resume([role("Dev", date(2010, 1, 1), date(2012, 1, 1), ["Built Python"])])
        mid = resume([role("Dev", date(2022, 1, 1), date(2023, 6, 1), ["Built Python"])])
        new = resume([role("Dev", date(2025, 1, 1), None, ["Built Python"])])
        got = [_match(x, job(["python"])).requirements[0].strength for x in (old, mid, new)]
        assert got == [0.7, 0.85, 1.0]

    def test_recency_windows_are_policy(self) -> None:
        mid = resume([role("Dev", date(2019, 1, 1), date(2021, 1, 1), ["Built Python"])])
        wide = Policy(recent_months=120)
        assert _match(mid, job(["python"]), wide).requirements[0].strength == 1.0

    def test_required_years_shortfall(self) -> None:
        r = resume([role("Dev", date(2025, 1, 1), None, ["Built Python"])])
        j = job(["python"], years={"python": 5})
        res = _match(r, j)
        row = res.requirements[0]
        assert row.strength < 0.6 and "years" in row.note
        assert any("years" in g for g in res.gaps)

    def test_experience_seniority_education_components(self) -> None:
        r = resume([role("Developer", date(2024, 1, 1), None, ["Built Python"])], education_level=2)
        j = job(["python"], min_years=6, seniority=3, education_level=3)
        res = _match(r, j)
        comps = {c.name: c.score for c in res.components}
        assert 0.3 < comps["experience"] < 0.5
        assert comps["seniority"] == 0.5 and comps["education"] == 0.5
        assert len(res.gaps) >= 3

    def test_no_penalty_for_overqualified(self) -> None:
        r = resume(
            [role("Principal Engineer", date(2005, 1, 1), None, ["Built Python"])],
            education_level=4,
        )
        res = _match(r, job(["python"], min_years=2, seniority=1, education_level=2))
        assert res.score == 100 and res.label == "strong"

    def test_weights_renormalize(self) -> None:
        r = resume([role("Dev", bullets=["Built Python"])])
        res = _match(r, job(["python"]))
        assert [c.name for c in res.components] == ["must-have skills"]
        assert res.score == 100

    def test_policy_weights_change_score(self) -> None:
        r = resume([role("Dev", bullets=["Built Python"])])
        j = job(["python"], nice=["rust"])
        assert _match(r, j, Policy(weight_nice=2.0)).score < _match(r, j).score

    def test_labels_and_thresholds(self) -> None:
        r = resume([role("Dev", bullets=["Built Python"])], skills_listed=["rust"])
        j = job(["python", "rust", "go"])
        assert _match(r, j).label == "possible"
        assert _match(r, j, Policy(possible_min=90, strong_min=95)).label == "weak"
        assert _match(resume(), job(["python"])).label == "weak"

    def test_context_requirements_are_not_scored(self) -> None:
        j = job(["python"], context=["rust"])
        res = _match(resume([role("Dev", bullets=["Python"])]), j)
        assert [r.skill for r in res.requirements] == ["python"]

    def test_nothing_to_score(self) -> None:
        with pytest.raises(InputError):
            _match(resume(), job())

    def test_strengths_listed(self) -> None:
        r = resume([role("Dev", date(2020, 1, 1), None, ["Built Python"])])
        res = _match(r, job(["python"]))
        assert res.strengths and "Python" in res.strengths[0]

    def test_seniority_from_title_and_years(self) -> None:
        assert resume_seniority(resume(), 0) == 1
        assert resume_seniority(resume([role("Junior Dev")]), 12) == 4
        assert resume_seniority(resume([role("Lead Dev", None, None)]), 1) == 4
        r = resume(
            [role("Dev", date(2015, 1, 1), date(2018, 1, 1)), role("Senior Dev", date(2019, 1, 1))]
        )
        assert resume_seniority(r, 3) == 3

    def test_ignored_attributes_reported_not_scored(self) -> None:
        base = resume([role("Dev", bullets=["Built Python"])])
        flagged = base.model_copy(update={"ignored_attributes": ["age"], "name": "Zed"})
        a, b = _match(base, job(["python"])), _match(flagged, job(["python"]))
        assert a.score == b.score and b.ignored_attributes == ["age"]


class TestFairness:
    def test_scoring_view_strips_personal_data(self) -> None:
        r = ResumeParser(TAX).parse(RESUME_TEXT, "jane")
        view = scoring_view(r)
        assert view.name == view.email == view.phone == "" and view.education_lines == []
        assert all(x.company == "" for x in view.roles) and view.education_level == 3
        assert r.name == "Jane Roe" and r.roles[0].company == "Acme Corp"

    def test_perturbations_cover_all_attributes(self) -> None:
        r = ResumeParser(TAX).parse(RESUME_TEXT, "jane")
        names = [c for c, _ in perturbations(r, RESUME_TEXT)]
        assert any("name changed" in n for n in names)
        for expected in (
            "email and phone replaced",
            "employer names replaced",
            "school names replaced",
            "graduation years shifted by 7",
            "personal attributes added to header",
        ):
            assert expected in names

    def test_perturbation_of_bare_text(self) -> None:
        r = ResumeParser(TAX).parse("Python developer", "x")
        variants = perturbations(r, "Python developer")
        names = [c for c, _ in variants]
        assert names[-1] == "personal attributes added to header"
        assert not {"email and phone replaced", "employer names replaced"} & set(names)

    def test_bullets_are_left_alone(self) -> None:
        text = "Ann\n\nExperience\nDev at Docker (2020 - 2022)\n- Worked at Docker on Python\n"
        r = ResumeParser(TAX).parse(text, "a")
        replaced = dict(perturbations(r, text))["employer names replaced"]
        assert "- Worked at Docker on Python" in replaced
        assert "Dev at Employer" in replaced

    def test_scoring_ignores_employer_that_looks_like_a_skill(self) -> None:
        text = (
            "Ann\nann@example.org\n\nExperience\nDev at Docker Inc (2020 - 2022)\n- Wrote tests\n"
        )
        r = ResumeParser(TAX).parse(text, "a")
        res = _match(r, job(["containers"]))
        assert res.missing_musts == ["Containers"]


class TestReview:
    def _review(self, text: str, j: Job | None = None):  # type: ignore[no-untyped-def]
        r = ResumeParser(TAX).parse(text, "x")
        return ResumeReviewer(TAX).review(r, TODAY, j)

    def codes(self, text: str) -> set[str]:
        return {f.code for f in self._review(text).findings}

    def test_good_resume_is_quiet_on_structure(self) -> None:
        codes = self.codes(RESUME_TEXT)
        assert not codes & {
            "no_contact",
            "no_experience_section",
            "no_skills_section",
            "date_order",
        }

    def test_missing_contact_and_sections(self) -> None:
        codes = self.codes("Ann\n\nI like things.")
        assert {"no_contact", "no_experience_section", "no_skills_section", "too_short"} <= codes
        assert "no_email" in self.codes("Ann\n+1 202 555 0100\n")

    def test_bullet_quality_findings(self) -> None:
        text = (
            "Ann\nann@example.org\n\nExperience\nDev at Foo (2020 - 2022)\n"
            "- Responsible for reports\n- Worked on tools\n- "
            + "word " * 45
            + "\nOps (2018 - 2019)\n"
        )
        codes = self.codes(text)
        assert {"few_numbers", "weak_verbs", "long_bullets", "empty_role"} <= codes

    def test_date_problems(self) -> None:
        text = (
            "Ann\nann@example.org\n\nExperience\nDev at Foo (2022 - 2020)\n- Built things\n"
            "Ops at Bar (2030 - 2031)\n- Ran things\n"
            "Analyst at Baz (2005 - 2006)\n- Analysed things\n"
        )
        assert {"date_order", "future_date", "gap"} <= self.codes(text)
        undated = "Ann\nann@example.org\n\nExperience\nWaiter\n- Served\n"
        assert "undated_roles" in self.codes(undated)

    def test_gap_threshold_is_policy(self) -> None:
        text = "Ann\nann@example.org\n\nExperience\nA (2010 - 2011)\n- x\nB (2014 - 2015)\n- y\n"
        r = ResumeParser(TAX).parse(text, "x")
        big = ResumeReviewer(TAX, Policy(max_gap_months=60)).review(r, TODAY)
        small = ResumeReviewer(TAX).review(r, TODAY)
        assert "gap" not in {f.code for f in big.findings}
        assert "gap" in {f.code for f in small.findings}

    def test_keyword_gaps_and_listed_only(self) -> None:
        r = ResumeParser(TAX).parse(RESUME_TEXT, "x")
        rev = ResumeReviewer(TAX).review(r, TODAY, job(["python", "rust", "containers"]))
        assert rev.keyword_gaps == ["Rust"]
        assert "listed_not_shown" in {f.code for f in rev.findings}
        assert rev.total_bullets == 3 and rev.quantified_bullets == 2

    def test_long_resume(self) -> None:
        assert "too_long" in self.codes("Ann\nann@example.org\n" + "word " * 950)

    def test_findings_sorted_by_severity(self) -> None:
        sev = [f.severity for f in self._review("Ann\n\nnothing").findings]
        order = {"issue": 0, "warning": 1, "info": 2}
        assert sev == sorted(sev, key=order.__getitem__)


class TestConfig:
    def test_defaults_and_validation(self) -> None:
        assert Policy().weight_must == 0.55
        with pytest.raises(ValueError, match="possible_min"):
            Policy(possible_min=90, strong_min=80)
        with pytest.raises(ValueError, match="positive"):
            Policy(
                weight_must=0,
                weight_nice=0,
                weight_experience=0,
                weight_seniority=0,
                weight_education=0,
            )

    def test_load_policy(self, tmp_path: Path) -> None:
        assert load_policy(None) == Policy()
        path = tmp_path / "p.yaml"
        path.write_text("weight_nice: 0.3\n", encoding="utf-8")
        assert load_policy(path).weight_nice == 0.3
        path.write_text("", encoding="utf-8")
        assert load_policy(path) == Policy()

    @pytest.mark.parametrize("body", ["weight_nice: -1", "unknown: 1", "[unclosed", "- 1"])
    def test_load_policy_rejects(self, tmp_path: Path, body: str) -> None:
        path = tmp_path / "p.yaml"
        path.write_text(body, encoding="utf-8")
        with pytest.raises(ConfigurationError):
            load_policy(path)

    def test_load_policy_missing_and_huge(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigurationError):
            load_policy(tmp_path / "nope.yaml")
        big = tmp_path / "big.yaml"
        big.write_bytes(b"#" * 2_000_001)
        with pytest.raises(ConfigurationError, match="larger"):
            load_policy(big)

    def test_settings_retry_window(self) -> None:
        with pytest.raises(ValueError, match="retry_max_wait"):
            make_settings(retry_min_wait=5, retry_max_wait=1)
