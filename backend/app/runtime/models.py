"""Turning `model` in an agent configuration into an actual chat model.

One place, one function. The configuration names a provider and a model; this resolves
it. That indirection is what lets a configuration written by one company run in another
company's workspace, where a different provider may be configured.

DEMO-DAY INSURANCE
------------------
A rate limit during a live 45-minute presentation is not a hypothetical: one run
of a two-specialist agent costs about six requests, and an account that has been
exercised all morning can hit its per-minute ceiling.

So every model returned here is wrapped in a fallback chain: if the first model
returns 429, LangChain transparently retries the same request on the next one.
The run does not stop, and nobody watching notices.
"""

from __future__ import annotations

import logging

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import Runnable

from app.builder.schema import ModelSpec
from app.core.config import settings

log = logging.getLogger(__name__)

#: The one provider this platform runs on. Its key comes from .env (OPENAI_API_KEY).
PROVIDER = "openai"

#: Tried in order when the configuration's own model is unavailable or rate-limited.
#: Newest first, then one generation back - a 429 on the newest is the common case,
#: and the older models answer the same prompts with the same tool-calling shape.
FALLBACKS: list[tuple[str, str]] = [
    (PROVIDER, "gpt-6.1-sol"),
    (PROVIDER, "gpt-6-sol"),
    (PROVIDER, "gpt-5.6-sol"),
]


def _build(provider: str, name: str, temperature: float) -> BaseChatModel | None:
    if provider != PROVIDER or not settings.openai_api_key:
        return None
    try:
        return init_chat_model(name, model_provider=provider, temperature=temperature)
    except Exception as exc:  # noqa: BLE001 - any provider failure is just a fallback
        log.debug("model %s/%s unusable: %s", provider, name, exc)
        return None


def model_chain(spec: ModelSpec) -> list[BaseChatModel]:
    """The configuration's model first, then every configured alternative.

    Returned as a list rather than a single object so the caller can bind tools
    to each one before chaining them - `with_fallbacks()` returns a wrapper that
    no longer has `.bind_tools()`.
    """
    wanted = [(spec.provider, spec.name), *FALLBACKS]

    chain: list[BaseChatModel] = []
    seen: set[tuple[str, str]] = set()
    for provider, name in wanted:
        if (provider, name) in seen:
            continue
        seen.add((provider, name))
        if (model := _build(provider, name, spec.temperature)) is not None:
            chain.append(model)

    if not chain:
        raise RuntimeError("no usable model: put OPENAI_API_KEY in .env")
    return chain


def chat_model(spec: ModelSpec) -> Runnable:
    """A plain chat model that survives a rate limit."""
    primary, *rest = model_chain(spec)
    return primary.with_fallbacks(rest) if rest else primary


def chat_model_with_tools(spec: ModelSpec, tools: list[dict]) -> Runnable:
    """The same, with tools bound - bound to EVERY model in the chain.

    Binding before chaining matters: a fallback that has not been given the
    tools would quietly answer in prose instead of calling one.
    """
    bound = [m.bind_tools(tools) for m in model_chain(spec)]
    primary, *rest = bound
    return primary.with_fallbacks(rest) if rest else primary
