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
