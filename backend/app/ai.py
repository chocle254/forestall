"""Calls to the LLM. Every call asks for JSON and is validated against a schema.

Built to keep working when several people use the demo at once on a rate-limited (free) plan:
  - a chain of models: each has its own rate limit, so when one is busy the next takes over at once;
  - only when every model is busy do we wait (as long as the provider asks, bounded) and go round again;
  - compact prompts (schemas without titles, trimmed tool results) to stay inside tokens-per-minute limits.
"""

import asyncio
import json
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from .config import settings

T = TypeVar("T", bound=BaseModel)

MAX_WAIT_S = 20.0      # time we may wait for a rate limit to clear, per AI call (then the page retries)
TOOL_RESULT_CHARS = 3000


class AIUnavailable(Exception):
    """The model couldn't be reached, is rate limited, or kept returning bad JSON."""

    def __init__(self, message: str, busy: bool = False):
        super().__init__(message)
        self.busy = busy  # True when it's only rate limits: worth retrying in a few seconds


def api_key() -> str:
    return settings.ai_api_key or settings.groq_api_key


def configured() -> bool:
    return bool(api_key())


def model_name() -> str:
    return settings.ai_model


def models() -> list[str]:
    """Main model first, then the fallbacks, without duplicates."""
    chain = [settings.ai_model, *settings.ai_extra_models.split(","), settings.ai_fallback_model]
    out: list[str] = []
    for m in (x.strip() for x in chain):
        if m and m not in out:
            out.append(m)
    return out


def compact_schema(schema: type[BaseModel]) -> str:
    """The JSON schema without titles: same constraints, far fewer tokens."""
    def strip(node):
        if isinstance(node, dict):
            return {k: strip(v) for k, v in node.items() if k != "title"}
        if isinstance(node, list):
            return [strip(v) for v in node]
        return node
    return json.dumps(strip(schema.model_json_schema()), separators=(",", ":"))


def _retry_after(r: httpx.Response) -> float:
    try:
        return min(max(float(r.headers.get("retry-after", "4")), 1.0), 15.0)
    except ValueError:
        return 4.0


async def _send(body: dict) -> httpx.Response:
    async with httpx.AsyncClient(timeout=60) as client:
        return await client.post(f"{settings.ai_base_url}/chat/completions",
                                 headers={"Authorization": f"Bearer {api_key()}"}, json=body)


def _body(model: str, messages: list[dict], **extra) -> dict:
    body: dict = {"model": model, "messages": messages, "temperature": 0.2, **extra}
    if model.startswith("openai/gpt-oss"):
        body["reasoning_effort"] = "low"  # short answers; keeps us well inside free-tier token limits
    return body


async def _post_chain(messages: list[dict], start: str | None = None, **extra) -> tuple[httpx.Response, str]:
    """Send to the first model that isn't busy. Returns (response, model used).

    Rate limited / overloaded / retired -> next model at once. All busy -> wait and go round again.
    """
    chain = models()
    if start in chain:
        chain = chain[chain.index(start):] + chain[:chain.index(start)]
    waited, last = 0.0, None
    while True:
        wait = 15.0
        for m in chain:
            try:
                r = await _send(_body(m, messages, **extra))
            except httpx.HTTPError as e:
                last = f"{m}: request failed ({type(e).__name__})"
                continue
            if r.status_code == 429 or r.status_code >= 500 or r.status_code == 404:
                last = f"{m} returned {r.status_code}"
                if r.status_code == 429:
                    wait = min(wait, _retry_after(r))
                continue
            return r, m
        if waited + wait > MAX_WAIT_S:
            raise AIUnavailable(f"AI is busy (all models rate limited): {last}", busy=True)
        await asyncio.sleep(wait)
        waited += wait


# Model that produced the most recent answer (main or fallback), for the audit trail.
last_model_used: str = ""


async def chat_json(system: str, user: str, schema: type[T], attempts: int = 2) -> T:
    global last_model_used
    if not configured():
        raise AIUnavailable("AI is not configured (set GROQ_API_KEY)")
    messages = [
        {"role": "system", "content": system + "\n\nReply with a single JSON object only. JSON schema:\n"
         + compact_schema(schema)},
        {"role": "user", "content": user},
    ]
    last_error, model = "", None
    for _ in range(attempts):
        r, model = await _post_chain(messages, start=model, response_format={"type": "json_object"})
        if r.status_code >= 400:
            raise AIUnavailable(f"AI returned {r.status_code}: {r.text[:200]}")
        content = r.json()["choices"][0]["message"]["content"] or ""
        try:
            result = schema.model_validate_json(_strip_fences(content))
            last_model_used = model
            return result
        except ValidationError as e:
            last_error = str(e)[:300]
            # Show the model its mistake once.
            messages = messages[:2] + [
                {"role": "assistant", "content": content},
                {"role": "user", "content": f"That JSON did not match the schema: {last_error}. Reply again with valid JSON only."},
            ]
    raise AIUnavailable(f"AI kept returning invalid JSON: {last_error}")


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


# --- Agent loop: the model calls tools until it has enough to answer -----------

def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        t = t.rsplit("```", 1)[0]
    return t.strip()


async def run_agent(system: str, user: str, tools: list[dict], handlers: dict, schema: type[T],
                    max_steps: int = 6, preloaded: list[dict] | None = None) -> tuple[T, list[dict]]:
    """OpenAI-style tool calling. Returns (validated answer, trace of tool calls).

    preloaded: tool calls already made for the agent (e.g. the live carrier and PayPal reads every check needs),
    given to it as its own first tool calls so it doesn't spend a round trip asking for them.
    """
    global last_model_used
    if not configured():
        raise AIUnavailable("AI is not configured (set GROQ_API_KEY)")
    final_instr = ("\n\nCall several tools at once when you can. When you have enough evidence, stop calling tools "
                   "and write your answer as plain text in your reply: a single JSON object (no prose, and never a "
                   "tool call named json) matching this JSON schema:\n" + compact_schema(schema))
    messages: list[dict] = [{"role": "system", "content": system + final_instr}, {"role": "user", "content": user}]
    trace: list[dict] = []
    if preloaded:
        calls = [{"id": f"pre_{i}", "type": "function",
                  "function": {"name": p["tool"], "arguments": json.dumps(p.get("args") or {})}}
                 for i, p in enumerate(preloaded)]
        messages.append({"role": "assistant", "content": "", "tool_calls": calls})
        for c, p in zip(calls, preloaded):
            messages.append({"role": "tool", "tool_call_id": c["id"],
                             "content": json.dumps(p["result"], default=str)[:TOOL_RESULT_CHARS]})
            trace.append(p)
    model: str | None = None
    retried_tools = False

    for _ in range(max_steps):
        r, model = await _post_chain(messages, start=model, tools=tools, tool_choice="auto")
        if r.status_code == 400 and "tool_use_failed" in r.text:
            # The model tried to call a tool that doesn't exist (gpt-oss sometimes "calls" json).
            # It has finished investigating: write up the evidence gathered so far.
            if trace or retried_tools:
                break
            retried_tools = True
            messages.append({"role": "user", "content": "Use only the listed tools. Start with get_carrier_tracking."})
            continue
        if r.status_code >= 400:
            raise AIUnavailable(f"AI returned {r.status_code}: {r.text[:200]}")
        msg = r.json()["choices"][0]["message"]
        calls = msg.get("tool_calls") or []
        if not calls:
            content = _strip_fences(msg.get("content") or "")
            try:
                last_model_used = model
                return schema.model_validate_json(content), trace
            except ValidationError:
                break  # fall through to a strict JSON pass below
        messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
        for c in calls:
            name = c["function"]["name"]
            try:
                args = json.loads(c["function"].get("arguments") or "{}")
            except ValueError:
                args = {}
            handler = handlers.get(name)
            try:
                result = await handler(**args) if handler else {"error": f"unknown tool {name}"}
            except Exception as e:  # a tool failure is information for the agent, not a crash
                result = {"error": f"{type(e).__name__}: {str(e)[:200]}"}
            trace.append({"tool": name, "args": args, "result": result})
            messages.append({"role": "tool", "tool_call_id": c["id"],
                             "content": json.dumps(result, default=str)[:TOOL_RESULT_CHARS]})

    # Out of steps, or the last answer wasn't valid JSON: summarise the evidence into the schema.
    evidence = json.dumps(trace, default=str)[:9000]
    answer = await chat_json(system, f"{user}\n\nEvidence gathered by tool calls:\n{evidence}", schema)
    return answer, trace
