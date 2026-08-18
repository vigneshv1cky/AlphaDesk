"""Import side effects only: pulling this in registers everything shipped with
AlphaDesk. The registry imports it once, before any third-party plugin, so a
plugin registering the same name deliberately wins.
"""

from alphadesk.providers import llm, news, prices  # noqa: F401
