"""Import-and-call smoke tests.

These exist because of a real bug: a refactor deleted module-level constants
that sat BETWEEN functions, and every module still imported cleanly — the
breakage was a NameError that only fired when the function was actually
called. `import x` is not proof that `x` works.

Anything with a cheap, side-effect-free call path belongs here.
"""

import importlib
import pkgutil

import pytest

import alphadesk


def test_every_module_imports():
    failed = []
    for mod in pkgutil.walk_packages(alphadesk.__path__, prefix="alphadesk."):
        if ".ui" in mod.name or ".deploy" in mod.name:
            continue
        try:
            importlib.import_module(mod.name)
        except Exception as exc:                      # noqa: BLE001
            failed.append(f"{mod.name}: {exc}")
    assert not failed, "modules failed to import:\n" + "\n".join(failed)


@pytest.mark.parametrize("call", [
    # each of these referenced a module-level global that a refactor once ate
    lambda p: p.sector_change_pct(None),
    lambda p: p.liquidity_batch([]),
    lambda p: p.get_chart_series(""),
])
def test_price_helpers_do_not_raise_nameerror(call):
    from alphadesk.ingest import prices
    try:
        call(prices)
    except NameError:                                  # the bug this guards
        raise
    except Exception:
        pass          # network/credential failures are fine; NameError is not


def test_store_read_helpers_run_on_an_empty_ledger(store):
    assert store.earnings_reactions_batch([]) == {}
    assert store.recent_articles_by_ticker("2099-01-01T00:00:00Z") == {}
    assert store.upcoming_earnings(days=5) == []
    assert store.recently_reported(days=3) == []
    assert isinstance(store.news_health(), dict)
    assert store.token_summary(days=1) == []
