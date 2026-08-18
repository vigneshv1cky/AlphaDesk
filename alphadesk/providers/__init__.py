"""Provider plugins — the seams where AlphaDesk talks to the outside world.

Three things are pluggable: the LLM, the news feed, and market data. Each is a
Protocol in `base`, an implementation registered by name, and a config key that
picks which one runs. Nothing in the app imports a vendor directly any more; it
asks the registry.

Third-party packages plug in without touching this repo — see
`docs/providers.md`.
"""

from alphadesk.providers.base import (  # noqa: F401
    Article,
    ChatResult,
    LLMProvider,
    NewsProvider,
    PriceProvider,
    ProviderError,
)
from alphadesk.providers.registry import (  # noqa: F401
    available,
    get_llm,
    get_news,
    get_prices,
    register,
)
