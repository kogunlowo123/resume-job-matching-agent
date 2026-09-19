"""Shared fixtures and builders."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

import httpx

from jobmatch.config import Policy, Settings
from jobmatch.container import build_service
from jobmatch.models import Job, Requirement, Resume, Role
from jobmatch.providers.http import JsonClient
from jobmatch.service import MatchService
from jobmatch.summary import TemplateSummaryWriter

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIGS = REPO_ROOT / "configs"
SAMPLES = CONFIGS / "samples"
TODAY = date(2026, 9, 19)


def make_settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {"_env_file": None, "log_level": "CRITICAL"}
    base.update(overrides)
    return Settings(**base)


def make_service(policy: Policy | None = None, **overrides: Any) -> MatchService:
    if policy is not None:
        return MatchService(make_settings(**overrides), TemplateSummaryWriter(), policy=policy)
    return build_service(make_settings(**overrides))


def role(
    title: str = "Engineer",
    start: date | None = date(2020, 1, 1),
    end: date | None = None,
    bullets: list[str] | None = None,
    *,
    company: str = "Acme",
) -> Role:
    return Role(
        title=title,
        company=company,
        start=start,
        end=end,
        present=end is None and start is not None,
        bullets=bullets or [],
    )


def resume(roles: list[Role] | None = None, **fields: Any) -> Resume:
    data: dict[str, Any] = {"id": "r1", "roles": roles or []}
    data.update(fields)
    return Resume.model_validate(data)


def job(
    must: list[str] | None = None,
    nice: list[str] | None = None,
    *,
    context: list[str] | None = None,
    years: dict[str, float] | None = None,
    **fields: Any,
) -> Job:
    """A job with skill ids per priority. ``years`` gives per-skill minimum years."""
    years = years or {}
    requirements = [
        Requirement(skill=s, priority="must", min_years=years.get(s)) for s in must or []
    ]
    requirements += [Requirement(skill=s, priority="nice") for s in nice or []]
    requirements += [Requirement(skill=s, priority="context") for s in context or []]
    data: dict[str, Any] = {"id": "j1", "title": "Engineer", "requirements": requirements}
    data.update(fields)
    return Job.model_validate(data)


def json_client(
    handler: Callable[[httpx.Request], httpx.Response], attempts: int = 2
) -> JsonClient:
    """A JsonClient backed by an in-process mock transport."""
    return JsonClient(
        httpx.Client(transport=httpx.MockTransport(handler)),
        attempts=attempts,
        min_wait=0.0,
        max_wait=0.0,
    )
