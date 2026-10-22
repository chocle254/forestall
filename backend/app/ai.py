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


async def chat_json(system: str, user: str, schema: type[T], attempts: int = 2) -> T:
    if not configured():
        raise AIUnavailable("AI is not configured (set GROQ_API_KEY)")
    messages = [
        {"role": "system", "content": system + "\n\nReply with a single JSON object only. JSON schema:\n"
         + json.dumps(schema.model_json_schema())},
        {"role": "user", "content": user},
    ]
    last_error = ""
    for _ in range(attempts):
        try:
            async with httpx.AsyncClient(timeout=45) as client:
                r = await client.post(
                    f"{settings.ai_base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {api_key()}"},
                    json={
                        "model": settings.ai_model,
                        "messages": messages,
                        "temperature": 0.2,
                        "response_format": {"type": "json_object"},
                    },
                )
        except httpx.HTTPError as e:
            raise AIUnavailable(f"AI request failed: {type(e).__name__}") from e
        if r.status_code == 429:
            raise AIUnavailable("AI rate limit reached; try again in a minute")
        if r.status_code >= 400:
            raise AIUnavailable(f"AI returned {r.status_code}: {r.text[:200]}")
        content = r.json()["choices"][0]["message"]["content"]
        try:
            return schema.model_validate_json(content)
        except ValidationError as e:
            last_error = str(e)[:300]
            # Show the model its mistake once, then give up.
            messages += [
                {"role": "assistant", "content": content},
                {"role": "user", "content": f"That JSON did not match the schema: {last_error}. Reply again with valid JSON only."},
            ]
    raise AIUnavailable(f"AI returned invalid JSON: {last_error}")


async def check() -> str:
    if not configured():
        return "not_configured"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{settings.ai_base_url}/models", headers={"Authorization": f"Bearer {api_key()}"})
        return "ok" if r.status_code == 200 else f"error_{r.status_code}"
    except httpx.HTTPError:
        return "unreachable"
