# Contributing

Thanks for helping improve this project. This guide covers the workflow and the quality bar.

## Development setup

```bash
git clone <repository-url>
cd resume-job-matching-agent
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
python -m pip install -e ".[dev]"
```

## Checks

Every change must pass the gates CI enforces:

```bash
make lint        # ruff check + ruff format --check
make typecheck   # mypy --strict
make cov         # pytest with a coverage gate of 80%
```

`make format` applies safe autofixes and formatting.

## Workflow

1. Open an issue for anything larger than a small fix so the design can be discussed first.
2. Branch from `main`: `feature/<short-name>` or `fix/<short-name>`.
3. Keep commits focused, with imperative subjects.
4. Add or update tests. Bug fixes need a regression test that fails without the fix.
5. Update `CHANGELOG.md` under **Unreleased** and any affected documentation.
6. Open a pull request describing the problem, the approach and how you verified it.

## Code standards

- Python 3.10+, fully type-annotated, `mypy --strict` clean.
- Docstrings explain behaviour, not restate names.
- Errors raised deliberately derive from `MatchError`.
- Matching is pure. The same resume, job, policy, taxonomy and date give the same result.
- Scoring reads only `fairness.scoring_view(resume)`. A new field that describes a person rather than their work
  must be blanked there and covered by an `audit` perturbation.
- Every threshold is a `Policy` field with a default and a test that shows it changes the outcome.
- Anything taken from a resume or job that ends up in Markdown or CSV goes through `md_cell`, `md_code` or
  `csv_safe`. Quotes go through `scrub_pii` first.
- Never send names, contact details, employers or resume text to a language model.
- Tests are offline and deterministic. Use the builders in `tests/conftest.py`.

## Adding a skill

Add a `_s(...)` entry to `taxonomy._BUILTIN` (or a YAML entry for local use). Give the aliases people really
write. Use `implies` only when holding the skill is fair evidence for the other one, and `related` for
neighbours. Names that are also common words (Go, R) should be `case_sensitive`. `tests/unit/test_core.py`
checks that every reference resolves.

## Adding a scoring component

1. Add its weight to `Policy`, and its component to `Matcher.match` only when the job specifies it, so the
   others renormalise.
2. Explain it in `Component.detail` in plain words.
3. Test that the policy weight changes the score and that an unspecified requirement changes nothing.

## Reporting security issues

See [SECURITY.md](SECURITY.md). Do not file public issues for vulnerabilities.

## License

By contributing you agree that your contributions are licensed under the MIT License.
