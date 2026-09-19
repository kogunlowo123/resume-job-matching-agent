"""Composition root: builds a :class:`MatchService` from :class:`Settings`."""

from __future__ import annotations

import httpx

from jobmatch.config import Settings
from jobmatch.errors import ConfigurationError
from jobmatch.providers import AnthropicChatClient, JsonClient, LLMClient, OpenAIChatClient
from jobmatch.service import MatchService
from jobmatch.summary import LLMSummaryWriter, SummaryWriter, TemplateSummaryWriter


def _summary_writer(settings: Settings, http_client: httpx.Client | None) -> SummaryWriter:
    if settings.llm_provider == "none":
        return TemplateSummaryWriter()
    client = JsonClient(
        http_client or httpx.Client(timeout=settings.http_timeout_seconds),
        attempts=settings.retry_attempts,
        min_wait=settings.retry_min_wait,
        max_wait=settings.retry_max_wait,
    )
    llm: LLMClient
    if settings.llm_provider == "openai":
        if settings.openai_api_key is None:
            raise ConfigurationError("JOBMATCH_OPENAI_API_KEY must be set when llm_provider=openai")
        llm = OpenAIChatClient(
            client,
            api_key=settings.openai_api_key,
            model=settings.openai_chat_model,
            base_url=settings.openai_base_url,
        )
    else:
        if settings.anthropic_api_key is None:
            raise ConfigurationError(
                "JOBMATCH_ANTHROPIC_API_KEY must be set when llm_provider=anthropic"
            )
        llm = AnthropicChatClient(
            client,
            api_key=settings.anthropic_api_key,
            model=settings.anthropic_model,
            max_tokens=settings.anthropic_max_tokens,
            base_url=settings.anthropic_base_url,
        )
    return LLMSummaryWriter(llm)


def build_service(
    settings: Settings,
    *,
    http_client: httpx.Client | None = None,
    summary_writer: SummaryWriter | None = None,
) -> MatchService:
    """Assemble the dependency graph.

    Raises:
        ConfigurationError: If provider credentials or the taxonomy or policy files are invalid.
    """
    return MatchService(settings, summary_writer or _summary_writer(settings, http_client))
