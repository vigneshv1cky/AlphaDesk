"""LLM providers.

`openai-compatible` covers most of the field with one implementation —
DeepSeek, OpenAI, Groq, Together, LM Studio and Ollama all speak the same
/chat/completions shape, so switching between them is a base URL and a model
name, not new code. Anthropic gets its own provider because its API differs.

Both return a JSON object as text. AlphaDesk never asks a model for prose it
then has to parse, and never asks it to call a tool.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from alphadesk.providers.base import ChatResult, ProviderError
from alphadesk.providers.registry import register


def _post(url: str, payload: dict, headers: dict, timeout_s: float) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **headers}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        raise ProviderError(f"HTTP {exc.code}: {body}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ProviderError(f"request failed: {exc}") from exc


def _strip_fence(text: str) -> str:
    """Unwrap a ```json fence if the model added one. Prompted JSON is not a
    hard guarantee the way an API-level JSON mode is, and a fence is by far the
    most common way it is broken."""
    if not text.startswith("```"):
        return text
    body = text.split("\n", 1)[1] if "\n" in text else ""
    return body.rsplit("```", 1)[0].strip() if "```" in body else body.strip()


class OpenAICompatibleLLM:
    """Any endpoint speaking OpenAI's /chat/completions.

    Config:
      LLM_BASE_URL   e.g. https://api.deepseek.com/v1
                          https://api.openai.com/v1
                          http://localhost:11434/v1        (Ollama)
      LLM_MODEL      e.g. deepseek-chat, gpt-4o-mini, llama3.1
      LLM_API_KEY    omitted for most local servers
    """

    name = "openai-compatible"

    def __init__(self) -> None:
        # DEEPSEEK_* are read as fallbacks so an existing single-provider
        # deployment keeps working without touching its .env.
        self.base_url = (os.environ.get("LLM_BASE_URL")
                         or os.environ.get("DEEPSEEK_BASE_URL")
                         or "https://api.deepseek.com/v1").rstrip("/")
        self.model = (os.environ.get("LLM_MODEL")
                      or os.environ.get("DEEPSEEK_MODEL")
                      or "deepseek-chat")
        self.api_key = (os.environ.get("LLM_API_KEY")
                        or os.environ.get("DEEPSEEK_API_KEY") or "").strip()

    def chat_json(self, system: str, user: str, *, max_tokens: int = 2048,
                  timeout_s: float = 60.0) -> ChatResult:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        data = _post(
            f"{self.base_url}/chat/completions",
            {
                "model": self.model,
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": user}],
                "max_tokens": max_tokens,
                "stream": False,
                "response_format": {"type": "json_object"},
            },
            headers, timeout_s,
        )
        text = ((data.get("choices") or [{}])[0].get("message") or {}).get("content", "").strip()
        if not text:
            raise ProviderError("empty completion")
        usage = data.get("usage") or {}
        return ChatResult(
            text=text,
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
            model=self.model,
        )


class AnthropicLLM:
    """Anthropic Messages API.

    Config: LLM_API_KEY (or ANTHROPIC_API_KEY), LLM_MODEL, LLM_BASE_URL.

    Three things here are not obvious, and two of them are load-bearing:

    1. **No assistant prefill.** This used to force JSON by prefilling the
       assistant turn with `{`. That is rejected with a 400 on every current
       model, so the JSON contract is now carried by the prompt — which every
       caller already states ("Return ONLY JSON: {...}"), reinforced below and
       defended by `_strip_fence`. Anthropic's real structured-output feature
       (`output_config.format`) needs a per-call JSON Schema, and `chat_json`
       is deliberately schema-free: four call sites, four different shapes, one
       transport. Adding schemas would mean changing the provider Protocol for
       every provider to serve one of them.
    2. **Thinking is on by default** on the current models and its tokens come
       out of `max_tokens`. A caller asking for 1024 could spend all of it
       thinking and return nothing, so effort is pinned low (this is
       summarize-and-cite, not reasoning) and a floor is applied to the budget.
    3. Sampling parameters (`temperature` and friends) are rejected outright on
       current models, which is why none are sent.
    """

    name = "anthropic"

    # Enough headroom that thinking tokens cannot starve the answer. The news
    # path asks for 2048 and the ask paths for 1024-1536; all fit well inside.
    _MIN_MAX_TOKENS = 4096

    def __init__(self) -> None:
        self.base_url = (os.environ.get("LLM_BASE_URL") or "https://api.anthropic.com").rstrip("/")
        self.model = os.environ.get("LLM_MODEL") or "claude-opus-5"
        self.api_key = (os.environ.get("LLM_API_KEY")
                        or os.environ.get("ANTHROPIC_API_KEY") or "").strip()

    def chat_json(self, system: str, user: str, *, max_tokens: int = 2048,
                  timeout_s: float = 60.0) -> ChatResult:
        if not self.api_key:
            raise ProviderError("LLM_API_KEY (or ANTHROPIC_API_KEY) is not set")
        data = _post(
            f"{self.base_url}/v1/messages",
            {
                "model": self.model,
                "system": system + (
                    "\n\nRespond with a single raw JSON object and nothing else — "
                    "no prose before or after it, and no markdown code fence."
                ),
                "max_tokens": max(max_tokens, self._MIN_MAX_TOKENS),
                "output_config": {"effort": "low"},
                "messages": [{"role": "user", "content": user}],
            },
            {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"},
            timeout_s,
        )
        # A safety decline is an HTTP 200 with stop_reason "refusal" and no
        # usable content — surface it as the failure it is rather than letting
        # it fall through as "empty completion".
        if data.get("stop_reason") == "refusal":
            raise ProviderError("the model declined this request")
        parts = data.get("content") or []
        text = "".join(p.get("text", "") for p in parts
                       if isinstance(p, dict) and p.get("type") == "text").strip()
        if not text:
            raise ProviderError("empty completion")
        usage = data.get("usage") or {}
        return ChatResult(
            text=_strip_fence(text),
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            model=self.model,
        )


register("llm", OpenAICompatibleLLM.name, OpenAICompatibleLLM)
register("llm", AnthropicLLM.name, AnthropicLLM)
