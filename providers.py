"""
Multi-provider text-generation fallback chain for HelloFides Bot.

Every provider is called the same way: pass a prompt that asks for strict
JSON, get back a parsed Python object. Providers differ only in transport
(Gemini SDK vs plain HTTPS to an OpenAI-compatible endpoint) - that
difference is hidden behind call(provider_id, prompt).

Two error types matter to the caller (generator.py):
- RateLimited: a 429. Never retry the same provider; move to the next one
  and remember how long to stay away (cooldown_seconds).
- ProviderError: anything else (timeout, 5xx, bad/non-JSON output, missing
  key). The caller retries the same provider at most once, then moves on.
"""
import json
import os
import re

import requests
import truststore

truststore.inject_into_ssl()

from google import genai
from google.genai import types as genai_types
from google.genai import errors as genai_errors

DEFAULT_COOLDOWN_SECONDS = 300


class ProviderError(Exception):
    """Transient failure (timeout, 5xx, bad JSON, missing key). Eligible for one retry."""


class RateLimited(Exception):
    """429. Do not retry this provider now - move on and cool it down."""

    def __init__(self, retry_after_seconds):
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"rate limited, retry after {retry_after_seconds}s")


def _strip_fences(text):
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    return text.strip()


def _parse_json_payload(text):
    try:
        data = json.loads(_strip_fences(text))
    except (json.JSONDecodeError, TypeError) as exc:
        raise ProviderError(f"non-JSON response: {exc}") from exc
    return data


def _gemini_retry_seconds(exc):
    details = getattr(exc, "details", None) or {}
    try:
        for item in details.get("error", {}).get("details", []):
            delay = item.get("retryDelay")
            if delay:
                match = re.match(r"(\d+)", str(delay))
                if match:
                    return int(match.group(1))
    except AttributeError:
        pass
    return DEFAULT_COOLDOWN_SECONDS


def _call_gemini(cfg, prompt):
    api_key = os.environ.get(cfg["key_env"])
    if not api_key:
        raise ProviderError(f"{cfg['key_env']} is not set")
    client = genai.Client(api_key=api_key)
    try:
        response = client.models.generate_content(
            model=cfg["model"],
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.9,
            ),
        )
    except genai_errors.APIError as exc:
        if exc.code == 429:
            raise RateLimited(_gemini_retry_seconds(exc)) from exc
        raise ProviderError(f"{exc.code} {exc.status}") from exc
    except Exception as exc:  # network/timeout/etc.
        raise ProviderError(str(exc)) from exc
    return _parse_json_payload(response.text)


def _call_openai_compatible(cfg, prompt):
    """Shared transport for Groq and Mistral - both speak the same
    OpenAI-style /chat/completions JSON over plain HTTPS."""
    api_key = os.environ.get(cfg["key_env"])
    if not api_key:
        raise ProviderError(f"{cfg['key_env']} is not set")

    try:
        resp = requests.post(
            cfg["endpoint"],
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": cfg["model"],
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.9,
                "response_format": {"type": "json_object"},
            },
            timeout=30,
        )
    except requests.RequestException as exc:
        raise ProviderError(f"request failed: {exc}") from exc

    if resp.status_code == 429:
        retry_after = resp.headers.get("Retry-After")
        seconds = int(retry_after) if retry_after and retry_after.isdigit() else DEFAULT_COOLDOWN_SECONDS
        raise RateLimited(seconds)
    if resp.status_code >= 400:
        raise ProviderError(f"HTTP {resp.status_code}")

    try:
        content = resp.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, ValueError) as exc:
        raise ProviderError(f"unexpected response shape: {exc}") from exc
    return _parse_json_payload(content)


_DISPATCH = {
    "gemini": _call_gemini,
    "groq": _call_openai_compatible,
    "mistral": _call_openai_compatible,
}


def call(provider_cfg, prompt):
    """Calls one provider. Returns a parsed JSON object (dict or list).
    Raises RateLimited or ProviderError - never a raw SDK/HTTP exception."""
    kind = provider_cfg["kind"]
    handler = _DISPATCH.get(kind)
    if handler is None:
        raise ProviderError(f"unknown provider kind '{kind}'")
    data = handler(provider_cfg, prompt)
    if isinstance(data, dict) and "posts" in data:
        return data["posts"]
    return data
