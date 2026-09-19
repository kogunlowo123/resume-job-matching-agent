"""Exception hierarchy for jobmatch."""

from __future__ import annotations


class MatchError(Exception):
    """Base class for errors raised deliberately by this package."""


class ConfigurationError(MatchError):
    """Settings or a configuration file are missing or invalid."""


class InputError(MatchError):
    """A resume, job or taxonomy file cannot be read or fails validation."""


class ReportError(MatchError):
    """A report could not be rendered or written."""


class ProviderError(MatchError):
    """An external model provider returned an error or an unusable response."""


class TransientProviderError(ProviderError):
    """A provider failure worth retrying (timeouts, rate limits, 5xx)."""
