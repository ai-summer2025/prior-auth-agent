"""Minimal, dependency-free LLM clients plus helpers for grounded JSON output.

Configure with environment variables:

  LLM_PROVIDER       "anthropic" (default) or "openai" (any OpenAI-compatible
                     server, including a local Ollama or vLLM instance)
  LLM_MODEL          model name (default: claude-haiku-5-5 for anthropic)
  ANTHROPIC_API_KEY  required for provider=anthropic
  LLM_BASE_URL       for provider=openai, e.g. http://localhost:11434/v1
  OPENAI_API_KEY     optional for provider=openai (local servers ignore it)

PHI note: with a hosted provider, note text leaves your machine. For real
patient data use a local model (provider=openai + Ollama/vLLM) or a provider
under a BAA. The bundled data is synthetic.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
from typing import Any, Optional


class LLMError(RuntimeError):
    pass


class AnthropicClient:
    def __init__(self, model: Optional[str] = None, api_key: Optional[str] = None,
                 timeout: float = 60.0):
        self.model = model or os.environ.get("LLM_MODEL", "claude-haiku-5-5")
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise LLMError("ANTHROPIC_API_KEY is not set")
        self.timeout = timeout

    def complete(self, system: str, user: str) -> str:
        body = json.dumps({
            "model": self.model, "max_tokens": 4000, "temperature": 0,
            "system": system, "messages": [{"role": "user", "content": user}],
        }).encode()
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages", data=body, method="POST",
            headers={"content-type": "application/json", "x-api-key": self.api_key,
                     "anthropic-version": "2023-06-01"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data = json.loads(r.read())
        except Exception as e:  # network, HTTP or JSON errors
            raise LLMError(f"Anthropic request failed: {e}") from e
        return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")


class OpenAICompatibleClient:
    def __init__(self, model: Optional[str] = None, base_url: Optional[str] = None,
                 api_key: Optional[str] = None, timeout: float = 120.0):
        self.model = model or os.environ.get("LLM_MODEL", "llama3.1")
        self.base_url = (base_url or os.environ.get("LLM_BASE_URL", "http://localhost:11434/v1")).rstrip("/")
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "not-needed")
        self.timeout = timeout

    def complete(self, system: str, user: str) -> str:
        body = json.dumps({
            "model": self.model, "temperature": 0,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }).encode()
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions", data=body, method="POST",
            headers={"content-type": "application/json", "authorization": f"Bearer {self.api_key}"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data = json.loads(r.read())
        except Exception as e:
            raise LLMError(f"LLM request to {self.base_url} failed: {e}") from e
        return data["choices"][0]["message"]["content"]


def client_from_env():
    provider = os.environ.get("LLM_PROVIDER", "anthropic").lower()
    if provider == "openai":
        return OpenAICompatibleClient()
    return AnthropicClient()


def parse_json(raw: str, kind: type = list) -> Any:
    """Parse the first JSON array/object in a model reply; tolerate code fences. Returns kind() on failure."""
    raw = re.sub(r"```(?:json)?", "", raw or "").strip()
    open_ch, close_ch = ("[", "]") if kind is list else ("{", "}")
    start, end = raw.find(open_ch), raw.rfind(close_ch)
    if start == -1 or end == -1:
        return kind()
    try:
        data = json.loads(raw[start:end + 1])
    except json.JSONDecodeError:
        return kind()
    return data if isinstance(data, kind) else kind()


def _squash(s: str) -> str:
    return " ".join(str(s).lower().split())


def is_grounded(evidence: Any, source: str) -> bool:
    """True when `evidence` is a non-empty exact (whitespace/case-insensitive) substring of `source`."""
    ev = _squash(evidence or "")
    return bool(ev) and ev in _squash(source)
