# Security Policy

## Supported versions

Security fixes are released for the latest minor version on the `main` branch.

| Version | Supported |
| ------- | --------- |
| 0.1.x   | Yes       |

## Reporting a vulnerability

Do not open a public issue for security reports. Use GitHub's private vulnerability reporting (the
**Report a vulnerability** button on this repository's **Security** tab) and include a description and
impact, the affected version or commit, and a minimal reproduction. Please remove real personal data first.
You can expect an acknowledgement within 3 business days and a triage decision within 10 business days.

## Trust boundary

| Input | Trust |
| ----- | ----- |
| Resumes and job descriptions | Untrusted. They can contain markup, formulas, prompt-injection text and personal data |
| Policy and taxonomy files | Trusted operator input, parsed safely and validated |
| Model output used for summaries | Untrusted text, accepted only if grounded in supplied figures |

## Security controls

| Threat | Control | Location |
| ------ | ------- | -------- |
| Markup injection into reports | Table cells escaped, evidence in sanitised code spans, HTML characters encoded | `security.py`, `reporting.py` |
| Spreadsheet formula injection in CSV | Cells starting with `=`, `+`, `-`, `@`, tab or carriage return get a leading quote | `security.csv_safe` |
| Oversized or binary input | Per-file byte limit, file count limit, UTF-8 only, `.txt` and `.md` for resumes | `service.py` |
| Code execution through configuration | `yaml.safe_load` only, 2 MB limit, strict models that forbid unknown fields | `config.py`, `taxonomy.py` |
| Personal data in evidence quotes | E-mail addresses, phone numbers and profile links are replaced before a quote is shown | `security.scrub_pii` |
| Prompt injection into summaries | The model receives only scores, counts and skill names. Output containing numbers absent from them is discarded | `summary.py` |
| Leaky error messages | Errors name the file and the problem, never file content. CLI errors are redacted | `cli.py`, `security.redact` |
| Vulnerable dependencies | `pip-audit`, Dependabot, CodeQL | `.github/` |

## Known limits

- Reports quote short excerpts of resumes. Handle them like the resumes themselves.
- The tool has no network access unless a model provider is configured, and even then it sends no resume text.
