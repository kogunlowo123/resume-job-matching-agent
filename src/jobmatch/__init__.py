"""Explainable resume and job matching."""

from jobmatch._version import __version__
from jobmatch.config import Policy, Settings, load_policy
from jobmatch.container import build_service
from jobmatch.matching import Matcher
from jobmatch.models import Job, MatchResult, Resume
from jobmatch.service import MatchService

__all__ = [
    "Job",
    "MatchResult",
    "MatchService",
    "Matcher",
    "Policy",
    "Resume",
    "Settings",
    "__version__",
    "build_service",
    "load_policy",
]
