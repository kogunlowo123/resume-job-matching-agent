# ADR 0001: Score evidence, not keyword presence

- Status: Accepted
- Date: 2026-09-19

## Context

Keyword matching rewards resumes that list every popular tool. A skills list costs nothing to write, so it is a
weak signal, and a person who has really used a skill for three years describes what they did with it.

## Decision

A skill mentioned in a role title or bullet is *demonstrated*, keeps the quote as evidence and carries the
years of that role, merged so overlapping roles are not double counted. A skill that only appears in a skills
list, summary or certification is *listed* and counts for 0.4 of a demonstrated one. Skills implied by shown
skills (Kubernetes suggests containers, Flask suggests Python) count for 0.8 of their source. Related skills
(PostgreSQL for MySQL) get partial credit but never count as meeting a must-have. Recent use counts fully and
older use is discounted. The experience component counts only years in roles that show a wanted skill.

## Consequences

- A resume stuffed with keywords and no matching role history scores as weak, and the report says why.
- Every point of the score can be traced to a quote or to a missing quote.
- People who use different words for the same skill rely on the taxonomy's aliases, which can be extended.
