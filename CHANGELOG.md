# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses semantic versioning.

## [Unreleased]

## [0.1.0]

### Added

- Parsers for plain-text and Markdown resumes and job descriptions, and structured JSON or YAML jobs.
- A skills taxonomy with aliases, implied and related skills, case-sensitive names such as Go, and a negation
  guard, extendable from a YAML file.
- Evidence-based extraction: skills shown in role bullets count more than skills that are only listed, with
  years per skill from merged role dates and a recency factor.
- A transparent score with a per-component breakdown, per-requirement evidence quotes and a list of gaps.
- Fairness safeguards: scoring reads a view without names, contact details, employer or school names, and an
  `audit` command that changes those attributes and reports the score difference.
- Resume review covering contact details, sections, dates, gaps, quantified bullets, vague openers and keyword
  gaps against a job.
- Candidate and job ranking, Markdown, JSON and CSV reports, an optional grounded model summary, a
  command-line interface, Docker image and CI workflows.
