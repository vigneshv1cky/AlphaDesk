"""The option-chain join.

The fetch is two upstream calls that know different halves — the contracts
endpoint knows strike, side and open interest; the chain snapshot knows the
quote. `_merge` is where they meet, and the rules it enforces are the ones a
reader would be misled by if they broke.
"""

from types import SimpleNamespace

from alphadesk.ingest import options


def contract(sym, strike, side, oi=0):
    return SimpleNamespace(symbol=sym, strike_price=strike, type=side, open_interest=oi)


def snap(bid=None, ask=None, last=None):
    return SimpleNamespace(
        latest_quote=SimpleNamespace(bid_price=bid, ask_price=ask) if (bid or ask) else None,
        latest_trade=SimpleNamespace(price=last) if last else None,
    )


class TestMerge:
    def test_sides_split_and_each_is_strike_ordered(self):
        cs = [contract("C3", 30, "call"), contract("P1", 10, "put"),
              contract("C1", 10, "call"), contract("P2", 20, "put"),
              contract("C2", 20, "call")]
        calls, puts = options._merge(cs, {})
        # A chain is a price ladder; any order but ascending strike destroys the
        # only structure it has.
        assert [r["strike"] for r in calls] == [10, 20, 30]
        assert [r["strike"] for r in puts] == [10, 20]

    def test_mid_needs_both_sides(self):
        cs = [contract("A", 10, "call"), contract("B", 20, "call"), contract("C", 30, "call")]
        snaps = {"A": snap(bid=1.0, ask=1.4), "B": snap(bid=2.0), "C": snap(ask=3.0)}
        calls, _ = options._merge(cs, snaps)
        by = {r["strike"]: r for r in calls}
        assert by[10]["mid"] == 1.2
        # One-sided books are normal far from the money. A mid off a single side
        # is an invented price, not a wide one, so it stays null.
        assert by[20]["mid"] is None and by[20]["bid"] == 2.0
        assert by[30]["mid"] is None and by[30]["ask"] == 3.0

    def test_a_contract_with_no_snapshot_still_renders(self):
        """Open interest and strike come from the contract, not the quote — a
        contract nobody is quoting is a real row, not a dropped one."""
        calls, _ = options._merge([contract("X", 50, "call", oi=1234)], {})
        assert len(calls) == 1
        row = calls[0]
        assert row["open_interest"] == 1234 and row["strike"] == 50.0
        assert row["bid"] is None and row["ask"] is None and row["mid"] is None

    def test_open_interest_missing_is_zero_not_none(self):
        """The column is a count. None would render as a dash and read as
        'unknown' when the feed's own answer is 'none outstanding'."""
        calls, _ = options._merge([contract("Y", 5, "call", oi=None)], {})
        assert calls[0]["open_interest"] == 0

    def test_contract_type_enum_repr_is_handled(self):
        """alpaca-py hands back ContractType.CALL, not the string 'call'."""
        cs = [contract("Z", 5, "ContractType.CALL"), contract("W", 5, "ContractType.PUT")]
        calls, puts = options._merge(cs, {})
        assert len(calls) == 1 and len(puts) == 1
