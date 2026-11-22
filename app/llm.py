"""LLM access with structured (JSON) output, provider-independent, with a call log.

Providers, chosen by LLM_PROVIDER or automatically by which key is set:
* ``deepseek`` / ``openai`` — any OpenAI-compatible endpoint (DeepSeek by default, also vLLM / Ollama /
  other hosted models): DEEPSEEK_API_KEY or LLM_API_KEY, LLM_BASE_URL, LLM_MODEL.
* ``anthropic`` — Claude via the official SDK: ANTHROPIC_API_KEY, CLAUDE_MODEL.

Used for the research profile (stage 1) and for re-ranking ITMO units (stage 2). Every answer is validated
against a pydantic schema; an invalid answer is retried once with the validation error.

Observability: inside ``with recording() as calls:`` every request is recorded — purpose, model, prompt,
raw answers of every attempt, validation errors, tokens, time, estimated cost, status. The pipeline stores
the summary in the dossier (``Dossier.ai_calls``) and the full record in data/llm_logs/<dossier>.json.
"""
from __future__ import annotations

import json
import logging
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from typing import Iterator, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app import config

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

_recorder: ContextVar[list[dict] | None] = ContextVar("llm_recorder", default=None)


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


@contextmanager
def recording() -> Iterator[list[dict]]:
    """Collect full records of every LLM call made inside the block (per thread / job)."""
    calls: list[dict] = []
    token = _recorder.set(calls)
    try:
        yield calls
    finally:
        _recorder.reset(token)


def estimate_cost(input_tokens: int | None, output_tokens: int | None) -> float | None:
    if input_tokens is None or output_tokens is None:
        return None
    if not (config.LLM_PRICE_INPUT_PER_M or config.LLM_PRICE_OUTPUT_PER_M):
        return None
    return round(input_tokens / 1e6 * config.LLM_PRICE_INPUT_PER_M
                 + output_tokens / 1e6 * config.LLM_PRICE_OUTPUT_PER_M, 6)


def structured(system: str, user: str, schema: type[T], max_tokens: int = 8000,
               purpose: str = "") -> tuple[T, str]:
    """Returns (validated object, "<provider>:<model>") and records the call if a recorder is active."""
    p = provider()
    if not p:
        raise LLMError("LLM не настроена")
    rec = {
        "purpose": purpose, "provider": p, "model": model_name(),
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "system": system, "user": user, "schema": schema.__name__,
        "attempts": [], "status": "failed", "error": "", "input_tokens": 0, "output_tokens": 0,
    }
    t0 = time.monotonic()
    try:
        if p == "anthropic":
            obj, used = _anthropic(system, user, schema, max_tokens, rec)
        else:
            obj, used = _openai_compatible(system, user, schema, max_tokens, rec)
        rec["status"] = "ok" if len(rec["attempts"]) <= 1 else "retried"
        rec["model"] = used.split(":", 1)[-1]
        return obj, used
    except Exception as e:
        rec["error"] = f"{type(e).__name__}: {e}"[:1000]
        raise
    finally:
        rec["seconds"] = round(time.monotonic() - t0, 2)
        rec["cost_usd"] = estimate_cost(rec["input_tokens"], rec["output_tokens"])
        calls = _recorder.get()
        if calls is not None:
            calls.append(rec)
        log.info("LLM %s %s: %s, %.1fs, %s in / %s out tokens", purpose, rec["model"], rec["status"],
                 rec["seconds"], rec["input_tokens"], rec["output_tokens"])


# --------------------------------------------------------------------- OpenAI-compatible (DeepSeek, ...)
def _openai_compatible(system: str, user: str, schema: type[T], max_tokens: int, rec: dict) -> tuple[T, str]:
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
                "response_format": {"type": "json_object"},
                "stream": True, "stream_options": {"include_usage": True}}
        try:
            content, usage, finish, model = _stream_with_retry(url, body, headers)
        except LLMError as e:
            rec["attempts"].append({"http_status": getattr(e, "status", None), "answer": "", "validation_error": "",
                                    "error": str(e)[:500]})
            raise
        rec["input_tokens"] += int(usage.get("prompt_tokens") or 0)
        rec["output_tokens"] += int(usage.get("completion_tokens") or 0)
        attempt_rec = {"http_status": 200, "answer": content, "validation_error": "", "finish_reason": finish}
        rec["attempts"].append(attempt_rec)
        try:
            obj = schema.model_validate_json(content)
            return obj, f"{provider()}:{model or config.LLM_MODEL}"
        except ValidationError as e:
            last_error = str(e)[:1500]
            attempt_rec["validation_error"] = last_error
            log.info("LLM answer failed validation (attempt %d): %s", attempt + 1, last_error[:200])
            messages += [{"role": "assistant", "content": content},
                         {"role": "user", "content": f"Ответ не прошёл проверку схемы:\n{last_error}\nВерни исправленный JSON."}]
    raise LLMError(f"ответ не соответствует схеме: {last_error[:300]}")


class _HTTPError(LLMError):
    def __init__(self, status: int, text: str):
        super().__init__(f"HTTP {status}: {text[:300]}")
        self.status = status


def _stream_once(url: str, body: dict, headers: dict) -> tuple[str, dict, str, str]:
    """Streamed chat completion (server-sent events): data keeps flowing, so long answers are not cut off
    by idle timeouts of proxies or load balancers. Returns (content, usage, finish_reason, model)."""
    parts: list[str] = []
    usage: dict = {}
    finish, model = "", ""
    timeout = httpx.Timeout(config.LLM_TIMEOUT, connect=20.0, read=120.0)
    with httpx.stream("POST", url, json=body, headers=headers, timeout=timeout) as r:
        if r.status_code != 200:
            raise _HTTPError(r.status_code, r.read().decode(errors="ignore"))
        for line in r.iter_lines():
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            chunk = json.loads(payload)
            model = chunk.get("model") or model
            if chunk.get("usage"):
                usage = chunk["usage"]
            for ch in chunk.get("choices") or []:
                delta = ch.get("delta") or {}
                if delta.get("content"):
                    parts.append(delta["content"])
                if ch.get("finish_reason"):
                    finish = ch["finish_reason"]
    return "".join(parts), usage, finish, model


def _stream_with_retry(url: str, body: dict, headers: dict, attempts: int = 3) -> tuple[str, dict, str, str]:
    """Connection problems and 429/5xx are retried with a short back-off."""
    last: Exception | None = None
    for i in range(attempts):
        try:
            return _stream_once(url, body, headers)
        except _HTTPError as e:
            if e.status not in (429, 500, 502, 503, 504) or i == attempts - 1:
                raise
            last = e
        except (httpx.TransportError, json.JSONDecodeError) as e:
            last = e
        log.info("LLM request retry %d after %s", i + 1, type(last).__name__)
        time.sleep(3 * (i + 1))
    raise LLMError(f"{type(last).__name__}: {last}")


# --------------------------------------------------------------------- Anthropic (Claude)
def _anthropic(system: str, user: str, schema: type[T], max_tokens: int, rec: dict) -> tuple[T, str]:
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
    rec["input_tokens"] += response.usage.input_tokens or 0
    rec["output_tokens"] += response.usage.output_tokens or 0
    text = next((b.text for b in response.content if b.type == "text"), "")
    rec["attempts"].append({"http_status": 200, "answer": text, "validation_error": "",
                            "finish_reason": response.stop_reason})
    if response.stop_reason == "refusal" or response.parsed_output is None:
        raise LLMError(f"no structured output (stop_reason={response.stop_reason})")
    return response.parsed_output, f"anthropic:{response.model}"
