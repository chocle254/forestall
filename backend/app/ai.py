"""Calls to the LLM. Every call asks for JSON and is validated against a schema."""

import json
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from .config import settings

T = TypeVar("T", bound=BaseModel)


class AIUnavailable(Exception):
    """The model couldn't be reached, is rate limited, or kept returning bad JSON."""


def api_key() -> str:
    return settings.ai_api_key or settings.groq_api_key


def configured() -> bool:
    return bool(api_key())


def model_name() -> str:
    return settings.ai_model


async def _complete(model: str, messages: list[dict]) -> httpx.Response:
    body: dict = {
        "model": model,
        "messages": messages,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    if model.startswith("openai/gpt-oss"):
        body["reasoning_effort"] = "low"  # short answers; keeps us well inside free-tier token limits
    async with httpx.AsyncClient(timeout=60) as client:
        return await client.post(
            f"{settings.ai_base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key()}"},
            json=body,
        )


# Model that produced the most recent answer (main or fallback), for the audit trail.
last_model_used: str = ""


async def chat_json(system: str, user: str, schema: type[T], attempts: int = 2) -> T:
    global last_model_used
    if not configured():
        raise AIUnavailable("AI is not configured (set GROQ_API_KEY)")
    messages = [
        {"role": "system", "content": system + "\n\nReply with a single JSON object only. JSON schema:\n"
         + json.dumps(schema.model_json_schema())},
        {"role": "user", "content": user},
    ]
    models = [m for m in (settings.ai_model, settings.ai_fallback_model) if m]
    last_error = ""
    for model in models:
        for _ in range(attempts):
            try:
                r = await _complete(model, messages)
            except httpx.HTTPError as e:
                last_error = f"request failed: {type(e).__name__}"
                break  # try the fallback model
            if r.status_code in (404, 429) or r.status_code >= 500:
                last_error = f"{model} returned {r.status_code}"
                break  # retired / rate limited / overloaded: try the fallback model
            if r.status_code >= 400:
                raise AIUnavailable(f"AI returned {r.status_code}: {r.text[:200]}")
            content = r.json()["choices"][0]["message"]["content"] or ""
            try:
                result = schema.model_validate_json(content)
                last_model_used = model
                return result
            except ValidationError as e:
                last_error = str(e)[:300]
                # Show the model its mistake once, then move on.
                messages = messages[:2] + [
                    {"role": "assistant", "content": content},
                    {"role": "user", "content": f"That JSON did not match the schema: {last_error}. Reply again with valid JSON only."},
                ]
    raise AIUnavailable(f"AI unavailable: {last_error}")


async def check() -> str:
    if not configured():
        return "not_configured"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{settings.ai_base_url}/models", headers={"Authorization": f"Bearer {api_key()}"})
        return "ok" if r.status_code == 200 else f"error_{r.status_code}"
    except httpx.HTTPError:
        return "unreachable"


async def list_models() -> list[str]:
    """Model IDs available to this key (helps pick a replacement when one is retired)."""
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(f"{settings.ai_base_url}/models", headers={"Authorization": f"Bearer {api_key()}"})
    r.raise_for_status()
    return sorted(m["id"] for m in r.json().get("data", []))
