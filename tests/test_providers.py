"""The plugin seam: registration, discovery, selection, failure."""

import pytest

from alphadesk.providers import base, registry
from alphadesk.providers.base import Article, ChatResult, ProviderError


class FakeLLM:
    name = "fake-llm"

    def chat_json(self, system, user, *, max_tokens=2048, timeout_s=60.0):
        return ChatResult(text='{"ok": true}', input_tokens=3, output_tokens=1, model="fake")


class FakeNews:
    name = "fake-news"

    def fetch(self, since, limit=200):
        return [Article(id="1", title="t", url="u", published_at="2026-01-01T00:00:00Z",
                        symbols=["AAPL"])]


def test_fakes_satisfy_the_protocols():
    # runtime_checkable Protocols are the contract a third-party provider has
    # to meet without importing anything from AlphaDesk.
    assert isinstance(FakeLLM(), base.LLMProvider)
    assert isinstance(FakeNews(), base.NewsProvider)


def test_builtins_are_registered():
    got = registry.available()
    assert "openai-compatible" in got["llm"]
    assert "anthropic" in got["llm"]
    assert {"polygon", "alpaca"} <= set(got["news"])
    assert "builtin" in got["prices"]


def test_selection_follows_env(monkeypatch):
    registry.register("llm", "fake-llm", FakeLLM)
    monkeypatch.setenv("LLM_PROVIDER", "fake-llm")
    registry.reset_cache()
    assert registry.get_llm().name == "fake-llm"


def test_unknown_provider_names_what_is_available(monkeypatch):
    monkeypatch.setenv("NEWS_PROVIDER", "nope")
    registry.reset_cache()
    with pytest.raises(ProviderError) as exc:
        registry.get_news()
    msg = str(exc.value)
    # the error has to be actionable: what you asked for AND what exists
    assert "nope" in msg and "polygon" in msg


def test_registering_an_existing_name_overrides_it(monkeypatch):
    class Replacement(FakeNews):
        name = "polygon"

    registry.register("news", "polygon", Replacement)
    monkeypatch.setenv("NEWS_PROVIDER", "polygon")
    registry.reset_cache()
    assert isinstance(registry.get_news(), Replacement)


def test_bad_plugin_module_does_not_kill_startup(monkeypatch):
    monkeypatch.setenv("ALPHADESK_PLUGINS", "alphadesk.does_not_exist")
    registry._loaded = False
    registry.available()          # must not raise


class BareLLM:
    """A third-party provider is allowed to have no `.model` at all — the
    Protocol only requires `name` and `chat_json`."""

    name = "bare-llm"

    def chat_json(self, system, user, *, max_tokens=2048, timeout_s=60.0):
        return ChatResult(text="{}")


class TestModelLabel:
    """What gets stamped on a cached answer and on a row in the cost ledger.

    These used to be the literal string "deepseek-chat" at three call sites,
    which was true of exactly one deployment and wrong on every other. The
    label has to name the model that actually served the call, or /api/tokens
    attributes spend to a model nobody is running.
    """

    def test_it_names_the_selected_model(self, monkeypatch):
        from alphadesk.ai import llm
        monkeypatch.setenv("LLM_PROVIDER", "openai-compatible")
        monkeypatch.setenv("LLM_MODEL", "llama3.1")
        registry.reset_cache()
        assert llm.model_name() == "llama3.1"

    def test_it_follows_a_provider_switch(self, monkeypatch):
        from alphadesk.ai import llm
        monkeypatch.setenv("LLM_PROVIDER", "anthropic")
        monkeypatch.setenv("LLM_MODEL", "claude-sonnet-5")
        registry.reset_cache()
        assert llm.model_name() == "claude-sonnet-5"

    def test_a_provider_without_a_model_falls_back_to_its_name(self, monkeypatch):
        from alphadesk.ai import llm
        registry.register("llm", BareLLM.name, BareLLM)
        monkeypatch.setenv("LLM_PROVIDER", BareLLM.name)
        registry.reset_cache()
        assert llm.model_name() == "bare-llm", "never blank, never a guess"

    def test_no_vendor_name_is_hardcoded_at_a_call_site(self):
        """The regression this whole class exists for."""
        import pathlib
        desk = pathlib.Path(__file__).parent.parent / "alphadesk" / "desk"
        offenders = [p.name for p in desk.glob("*.py") if "deepseek" in p.read_text()]
        assert not offenders, f"hardcoded model name back in {offenders}"


class TestAnthropicRequestShape:
    """Pins the parts of the Anthropic request that the API rejects if wrong.

    Each of these was legal on an older model and is a 400 now, so a plausible
    "simplification" reintroduces a break that only shows up against the live
    endpoint — which no test here can reach.
    """

    def _sent(self, monkeypatch, **kwargs):
        from alphadesk.providers import llm as mod
        monkeypatch.setenv("LLM_API_KEY", "sk-test")
        monkeypatch.delenv("LLM_MODEL", raising=False)
        captured = {}

        def fake_post(url, payload, headers, timeout_s):
            captured.update(payload)
            return {"content": [{"type": "text", "text": '{"ok": true}'}],
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                    "stop_reason": "end_turn"}

        monkeypatch.setattr(mod, "_post", fake_post)
        result = mod.AnthropicLLM().chat_json("sys", "user", **kwargs)
        return captured, result

    def test_no_assistant_prefill(self, monkeypatch):
        payload, _ = self._sent(monkeypatch)
        roles = [m["role"] for m in payload["messages"]]
        assert roles == ["user"], "a trailing assistant prefill is a 400 on current models"

    def test_no_sampling_parameters(self, monkeypatch):
        payload, _ = self._sent(monkeypatch)
        assert not {"temperature", "top_p", "top_k"} & set(payload)

    def test_token_budget_has_room_for_thinking(self, monkeypatch):
        payload, _ = self._sent(monkeypatch, max_tokens=1024)
        assert payload["max_tokens"] >= 4096, "thinking tokens would starve the answer"
        assert payload["output_config"]["effort"] == "low"

    def test_a_fenced_reply_still_parses(self, monkeypatch):
        from alphadesk.providers import llm as mod
        monkeypatch.setenv("LLM_API_KEY", "sk-test")
        monkeypatch.setattr(mod, "_post", lambda *a, **k: {
            "content": [{"type": "text", "text": '```json\n{"ok": true}\n```'}],
            "usage": {}, "stop_reason": "end_turn"})
        import json
        assert json.loads(mod.AnthropicLLM().chat_json("s", "u").text) == {"ok": True}

    def test_a_refusal_is_not_reported_as_an_empty_completion(self, monkeypatch):
        from alphadesk.providers import llm as mod
        monkeypatch.setenv("LLM_API_KEY", "sk-test")
        monkeypatch.setattr(mod, "_post", lambda *a, **k: {
            "content": [], "usage": {}, "stop_reason": "refusal"})
        with pytest.raises(ProviderError, match="declined"):
            mod.AnthropicLLM().chat_json("s", "u")
