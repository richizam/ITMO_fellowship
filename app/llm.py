"""LLM access with structured (JSON) output, provider-independent.

Providers, chosen by LLM_PROVIDER or automatically by which key is set:
* ``deepseek`` / ``openai`` — any OpenAI-compatible endpoint (DeepSeek by default, also vLLM / Ollama /
  other hosted models): DEEPSEEK_API_KEY or LLM_API_KEY, LLM_BASE_URL, LLM_MODEL.
* ``anthropic`` — Claude via the official SDK: ANTHROPIC_API_KEY, CLAUDE_MODEL.

Used for the research profile (stage 1) and for re-ranking ITMO units (stage 2). Every answer is validated
against a pydantic schema; an invalid answer is retried once with the validation error.
"""
from __future__ import annotations

import json
import logging
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app import config

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    pass


def provider() -> str:
    p = config.LLM_PROVIDER
    if p:
        return p
    if config.ANTHROPIC_API_KEY:
        return "anthropic"
    if config.LLM_API_KEY:
        return "deepseek" if "deepseek" in config.LLM_BASE_URL else "openai"
    return ""


def enabled() -> bool:
    return bool(provider())


def model_name() -> str:
    return config.CLAUDE_MODEL if provider() == "anthropic" else config.LLM_MODEL


def label() -> str:
    names = {"anthropic": "Claude", "deepseek": "DeepSeek", "openai": "LLM"}
    return f"{names.get(provider(), provider())} ({model_name()})" if enabled() else ""


def structured(system: str, user: str, schema: type[T], max_tokens: int = 8000) -> tuple[T, str]:
    """Returns (validated object, "<provider>:<model>")."""
    p = provider()
    if not p:
        raise LLMError("LLM не настроена")
    if p == "anthropic":
        return _anthropic(system, user, schema, max_tokens)
    return _openai_compatible(system, user, schema, max_tokens)


# --------------------------------------------------------------------- OpenAI-compatible (DeepSeek, ...)
def _openai_compatible(system: str, user: str, schema: type[T], max_tokens: int) -> tuple[T, str]:
    schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False)
    messages = [
        {"role": "system", "content": f"{system}\n\nОтвечай только одним JSON-объектом, строго по этой JSON Schema "
                                      f"(все поля обязательны, без пояснений вне JSON):\n{schema_json}"},
        {"role": "user", "content": user},
    ]
    url = config.LLM_BASE_URL.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {config.LLM_API_KEY}"}
    last_error = ""
    for attempt in range(2):
        body = {"model": config.LLM_MODEL, "messages": messages, "max_tokens": max_tokens,
                "response_format": {"type": "json_object"}}
        r = _post_with_retry(url, body, headers)
        if r.status_code != 200:
            raise LLMError(f"HTTP {r.status_code}: {r.text[:300]}")
        data = r.json()
        choice = (data.get("choices") or [{}])[0]
        content = (choice.get("message") or {}).get("content") or ""
        try:
            obj = schema.model_validate_json(content)
            return obj, f"{provider()}:{data.get('model') or config.LLM_MODEL}"
        except ValidationError as e:
            last_error = str(e)[:1500]
            log.info("LLM answer failed validation (attempt %d): %s", attempt + 1, last_error[:200])
            messages += [{"role": "assistant", "content": content},
                         {"role": "user", "content": f"Ответ не прошёл проверку схемы:\n{last_error}\nВерни исправленный JSON."}]
    raise LLMError(f"ответ не соответствует схеме: {last_error[:300]}")


def _post_with_retry(url: str, body: dict, headers: dict, attempts: int = 3) -> httpx.Response:
    """Connection problems and 429/5xx are retried with a short back-off."""
    import time

    last: Exception | None = None
    for i in range(attempts):
        try:
            r = httpx.post(url, json=body, headers=headers, timeout=config.LLM_TIMEOUT)
            if r.status_code in (429, 500, 502, 503, 504) and i < attempts - 1:
                time.sleep(2 * (i + 1))
                continue
            return r
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.RemoteProtocolError) as e:
            last = e
            time.sleep(2 * (i + 1))
    raise LLMError(f"{type(last).__name__}: {last}")


# --------------------------------------------------------------------- Anthropic (Claude)
def _anthropic(system: str, user: str, schema: type[T], max_tokens: int) -> tuple[T, str]:
    import anthropic

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    response = client.beta.messages.parse(
        model=config.CLAUDE_MODEL,
        max_tokens=max_tokens,
        system=system,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "medium"},
        output_format=schema,
        messages=[{"role": "user", "content": user}],
    )
    if response.stop_reason == "refusal" or response.parsed_output is None:
        raise LLMError(f"no structured output (stop_reason={response.stop_reason})")
    return response.parsed_output, f"anthropic:{response.model}"
