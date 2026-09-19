# ADR 0003: A weighted linear score with visible parts

- Status: Accepted
- Date: 2026-09-19

## Context

Embedding similarity and learned rankers can order candidates well, but they cannot answer "why 68?" and are
hard to audit or tune. Hiring decisions get challenged.

## Decision

The score is `100 * sum(weight * component) / sum(weight)` over five components: must-have skills (0.55),
nice-to-have skills (0.15), relevant experience (0.15), seniority (0.10) and education (0.05). A component that
the job does not specify is left out and the rest renormalise. Being above a requirement is never penalised.
The label is `strong` only when the score is at least 75 and no must-have is missing. Weights and thresholds are
fields of a `Policy` that can be loaded from YAML.

## Consequences

- A person can recompute any score from the breakdown table.
- A tie is broken by fewer missing must-haves, then by id, so rankings are stable.
- The model cannot learn patterns a human did not put in, which is the intent, and also a limit on accuracy.
