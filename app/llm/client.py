"""OpenRouter client (OpenAI-compatible) with the pieces an agent harness needs:
  * model TIERS (fast / strong) with per-tier fallback chains
  * token + latency accounting (`sink`) so every call is attributable to an agent and a case
  * rate limiter (free-tier RPM) and a per-model circuit breaker (consecutive failures -> longer cooldown)
  * record/replay cassette (LLM_MODE=record|replay) so evals and tests are deterministic and offline
Free-tier models are frequently rate-limited (429) or overloaded (503 / error-in-200)."""
import asyncio
import hashlib
import json
import logging
import os
import re
import time
from collections import deque
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(ROOT / ".env")
URL = "https://openrouter.ai/api/v1/chat/completions"
CASSETTE = ROOT / "data" / "cassette.json"
log = logging.getLogger("uvicorn.error")
_cooldown = {}   # model -> epoch until which the breaker is open
_fails = {}      # model -> consecutive failures
_calls = deque() # timestamps of recent requests (rate limiter window)
_cassette = None


class LLMError(Exception):
    pass


def models(tier=None):
    key = {"fast": "OPENROUTER_MODELS_FAST", "strong": "OPENROUTER_MODELS_STRONG"}.get(tier)
    raw = (os.environ.get(key) if key else None) or os.environ.get("OPENROUTER_MODELS", "")
    return [m.strip() for m in raw.split(",") if m.strip()]


def mode():
    return os.environ.get("LLM_MODE", "live")


def configured():
    return mode() == "replay" or (bool(os.environ.get("OPENROUTER_API_KEY")) and bool(models()))


async def _rate_limit():
    rpm = int(os.environ.get("LLM_RPM", "18"))
    while True:
        now = time.time()
        while _calls and now - _calls[0] > 60:
            _calls.popleft()
        if len(_calls) < rpm:
            _calls.append(now)
            return
        await asyncio.sleep(max(0.2, 60 - (now - _calls[0])))


def _cassette_load():
    global _cassette
    if _cassette is None:
        _cassette = json.loads(CASSETTE.read_text()) if CASSETTE.exists() else {}
    return _cassette


def _key(messages, tools, tier):
    blob = json.dumps({"m": messages, "t": [t["function"]["name"] for t in tools or []], "tier": tier}, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


async def chat(messages, tools=None, max_tokens=2500, temperature=0.1, passes=3, timeout=120, tier="fast", sink=None):
    """Returns (assistant_message_dict, model_used). Raises LLMError when every model fails on every pass.
    `sink` (list) receives one usage dict per successful call."""
    key = _key(messages, tools, tier)
    if mode() == "replay":
        hit = _cassette_load().get(key)
        if not hit:
            raise LLMError("replay: no recorded response for this request")
        if sink is not None:
            sink.append({**hit["usage"], "replayed": True})
        return hit["msg"], hit["model"]
    if not configured():
        raise LLMError("OPENROUTER_API_KEY / OPENROUTER_MODELS not configured")
    headers = {"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}",
               "HTTP-Referer": "http://localhost:8000", "X-Title": "Junior AI Investigator"}
    errors = []
    async with httpx.AsyncClient(timeout=timeout) as client:
        for p in range(passes):
            for m in models(tier):
                if _cooldown.get(m, 0) > time.time() and p == 0:
                    errors.append(f"{m}: breaker open")
                    continue
                body = {"model": m, "messages": messages, "max_tokens": max_tokens, "temperature": temperature}
                if os.environ.get("OPENROUTER_REASONING", "off") == "off":
                    # measured: with reasoning on, free reasoning models spend the whole token budget thinking and
                    # return empty content (65-130 s); off gives a valid answer in ~20 s
                    body["reasoning"] = {"enabled": False}
                if tools:
                    body["tools"] = tools
                await _rate_limit()
                t0 = time.time()
                try:
                    r = await client.post(URL, headers=headers, json=body)
                    j = r.json()
                except Exception as e:  # network / decode
                    errors.append(f"{m}: {type(e).__name__} {e}")
                    log.warning("llm %s failed after %.0fs: %s %s", m, time.time() - t0, type(e).__name__, e)
                    _trip(m)
                    continue
                if "error" in j or r.status_code >= 400:
                    err = j.get("error", {})
                    code = err.get("code", r.status_code) if isinstance(err, dict) else r.status_code
                    errors.append(f"{m}: {code} {str(err)[:120]}")
                    log.warning("llm %s -> %s in %.0fs", m, code, time.time() - t0)
                    if code in (429, 503, 502, 500):
                        _trip(m)
                    if code in (401, 402, 403):
                        raise LLMError(f"auth/credit error from OpenRouter: {err}")
                    continue
                try:
                    msg = j["choices"][0]["message"]
                except (KeyError, IndexError):
                    errors.append(f"{m}: malformed response")
                    continue
                if not ((msg.get("content") or "").strip() or msg.get("tool_calls")):
                    errors.append(f"{m}: empty completion")
                    continue
                _fails[m] = 0
                u = j.get("usage") or {}
                usage = {"model": m, "tier": tier, "ms": int((time.time() - t0) * 1000),
                         "in": u.get("prompt_tokens") or sum(len(str(x.get("content", ""))) for x in messages) // 4,
                         "out": u.get("completion_tokens") or len(msg.get("content") or "") // 4}
                if sink is not None:
                    sink.append(usage)
                if mode() == "record":
                    c = _cassette_load(); c[key] = {"msg": msg, "model": m, "usage": usage}
                    CASSETTE.write_text(json.dumps(c))
                log.info("llm %s ok in %.0fs (in=%s out=%s tool_calls=%d)", m, time.time() - t0, usage["in"], usage["out"], len(msg.get("tool_calls") or []))
                return msg, m
            await asyncio.sleep(3 * (p + 1))
    raise LLMError("all models failed: " + " | ".join(errors[-6:]))


def _trip(m):
    """Circuit breaker: consecutive failures lengthen the cooldown (20s, 40s, 60s, 80s max)."""
    _fails[m] = _fails.get(m, 0) + 1
    _cooldown[m] = time.time() + 20 * min(_fails[m], 4)


def extract_json(text):
    """Pull the first JSON object out of a model reply (handles ```json fences and chatter)."""
    if not text:
        raise ValueError("empty reply")
    t = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", t, re.S)
    if fence:
        t = fence.group(1)
    start = t.find("{")
    if start < 0:
        raise ValueError("no JSON object in reply")
    depth, in_str, esc = 0, False, False
    for i in range(start, len(t)):
        ch = t[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(t[start:i + 1])
    raise ValueError("unterminated JSON object")
