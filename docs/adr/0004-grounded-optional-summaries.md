# ADR 0004: Model summaries are optional, minimal and grounded

- Status: Accepted
- Date: 2026-09-19

## Context

A short narrative helps a hiring manager, but a language model given a resume can repeat personal details, be
steered by text inside the resume and invent facts.

## Decision

The default summary is a deterministic template. If a provider is configured, the model receives a small JSON
object with the job title, score, label, counts, years and skill names. It never receives names, contact
details, employers or resume text. Its reply is accepted only if it is short and every number in it appears in
that JSON. Otherwise, and on any provider error, the template is used.

## Consequences

- Prompt injection inside a resume cannot reach the model.
- A model cannot state a score or a year count the tool did not produce.
- The summary is less fluent than an unconstrained one.
