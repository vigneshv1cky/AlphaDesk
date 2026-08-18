"""Provider registration, discovery and selection.

Three ways a provider gets in, in increasing order of decoupling:

  1. **Built in** — the implementations shipped in this package.
  2. **`ALPHADESK_PLUGINS`** — a comma-separated list of module paths that get
     imported at startup. Importing is enough; the module registers itself.
     This is the escape hatch for a local one-file provider.
  3. **Entry points** — a package declaring `[project.entry-points."alphadesk.providers"]`
     is discovered automatically once installed. This is how a real
     third-party provider ships, with no config at all.

Which one RUNS is a separate question from which are available, and is set by
`LLM_PROVIDER` / `NEWS_PROVIDER` / `PRICE_PROVIDER`.
"""

from __future__ import annotations

import functools
import importlib
import logging
import os
from typing import Any, Callable, Literal

from alphadesk.providers.base import LLMProvider, NewsProvider, PriceProvider, ProviderError

log = logging.getLogger("alphadesk.providers")

Kind = Literal["llm", "news", "prices"]

# kind -> name -> zero-arg factory. Factories, not instances: constructing a
# provider may read config or open a client, and that should not happen at
# import time for providers nobody selected.
_REGISTRY: dict[str, dict[str, Callable[[], Any]]] = {"llm": {}, "news": {}, "prices": {}}
_ENTRY_POINT_GROUP = "alphadesk.providers"
_loaded = False


def register(kind: Kind, name: str, factory: Callable[[], Any]) -> None:
    """Register a provider factory under `name`.

    Re-registering an existing name replaces it, which is what lets a fork or
    a local plugin override a built-in without patching this file.
    """
    if kind not in _REGISTRY:
        raise ValueError(f"unknown provider kind {kind!r}; expected one of {list(_REGISTRY)}")
    if name in _REGISTRY[kind]:
        log.info("provider %s/%s replaced", kind, name)
    _REGISTRY[kind][name] = factory


def _load_once() -> None:
    """Import built-ins, then env-listed modules, then installed entry points."""
    global _loaded
    if _loaded:
        return
    _loaded = True

    from alphadesk.providers import builtin  # noqa: F401  (registers on import)

    for mod in (m.strip() for m in os.environ.get("ALPHADESK_PLUGINS", "").split(",")):
        if not mod:
            continue
        try:
            importlib.import_module(mod)
            log.info("loaded plugin module %s", mod)
        except Exception as exc:                      # a bad plugin must not kill boot
            log.error("plugin %s failed to import: %s", mod, exc)

    try:
        from importlib.metadata import entry_points
        for ep in entry_points(group=_ENTRY_POINT_GROUP):
            try:
                ep.load()
                log.info("loaded provider entry point %s", ep.name)
            except Exception as exc:
                log.error("entry point %s failed: %s", ep.name, exc)
    except Exception as exc:                          # pragma: no cover
        log.debug("entry point discovery unavailable: %s", exc)


def available(kind: Kind | None = None) -> dict[str, list[str]]:
    """What's registered. Surfaced at /api/system so the terminal can show
    which providers a deployment actually has."""
    _load_once()
    kinds = [kind] if kind else list(_REGISTRY)
    return {k: sorted(_REGISTRY[k]) for k in kinds}


def _select(kind: Kind, env_var: str, default: str) -> Any:
    _load_once()
    name = os.environ.get(env_var, default).strip()
    impls = _REGISTRY[kind]
    if name not in impls:
        raise ProviderError(
            f"{env_var}={name!r} is not registered. Available {kind} providers: "
            f"{sorted(impls) or '(none)'}. Add one with ALPHADESK_PLUGINS or install "
            f"a package exposing the {_ENTRY_POINT_GROUP} entry point."
        )
    return impls[name]()


# Selected providers are cached: they may hold a client or a session, and the
# choice cannot change without a restart anyway. Tests call cache_clear().
@functools.lru_cache(maxsize=1)
def get_llm() -> LLMProvider:
    return _select("llm", "LLM_PROVIDER", "openai-compatible")


@functools.lru_cache(maxsize=1)
def get_news() -> NewsProvider:
    return _select("news", "NEWS_PROVIDER", "polygon")


@functools.lru_cache(maxsize=1)
def get_prices() -> PriceProvider:
    return _select("prices", "PRICE_PROVIDER", "builtin")


def reset_cache() -> None:
    """Forget the selected providers. For tests and for re-reading config."""
    get_llm.cache_clear()
    get_news.cache_clear()
    get_prices.cache_clear()
