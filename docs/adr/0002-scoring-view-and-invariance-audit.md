# ADR 0002: Score a restricted view and prove it with an audit

- Status: Accepted
- Date: 2026-09-19

## Context

Matching tools can encode bias through proxies: names, addresses, schools, employers, graduation years and
personal details some people put on a resume. Saying "we do not use those" is not something a reader can check.

## Decision

The matcher reads only `scoring_view(resume)`, a copy with the name, contact details, employer names, education
lines and detected personal attributes removed. Education level, dates and role text remain because they
describe work. The `audit` command re-scores variants of the original text with different names, contact
details, employers, schools and graduation years, and with date of birth, marital status and gender lines
added. It reports the score of each variant and exits 1 if any differs.

## Consequences

- The invariance claim is a test anyone can run on their own data.
- Attributes that appear in a resume are reported as "ignored", so the reader knows they were seen and not used.
- The audit covers what the scorer reads. It does not remove bias inherent in the taxonomy, the wording of a job
  or which skills a role's bullets happen to mention. The README says so.
