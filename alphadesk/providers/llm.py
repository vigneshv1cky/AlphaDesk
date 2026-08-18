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

    Anthropic has no `response_format`, so JSON is requested by prefilling the
    assistant turn with `{`. That constrains the very first token, which is a
    stronger guarantee than asking politely in the prompt — the reply is
    then completed FROM that brace, so it is reattached below.
    """

    name = "anthropic"

    def __init__(self) -> None:
        self.base_url = (os.environ.get("LLM_BASE_URL") or "https://api.anthropic.com").rstrip("/")
        self.model = os.environ.get("LLM_MODEL") or "claude-sonnet-4-5"
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
                "system": system,
                "max_tokens": max_tokens,
                "messages": [
                    {"role": "user", "content": user},
                    {"role": "assistant", "content": "{"},
                ],
            },
            {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"},
            timeout_s,
        )
        parts = data.get("content") or []
        text = "".join(p.get("text", "") for p in parts if isinstance(p, dict)).strip()
        if not text:
            raise ProviderError("empty completion")
        usage = data.get("usage") or {}
        return ChatResult(
            text="{" + text,        # reattach the prefill
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            model=self.model,
        )


register("llm", OpenAICompatibleLLM.name, OpenAICompatibleLLM)
register("llm", AnthropicLLM.name, AnthropicLLM)
