"""Socket deadlines on the vendor SDKs.

The failure this guards is quiet: an unbounded request does not error, it
parks one of Starlette's 40 threadpool workers until the OS gives up. Enough of
them and every endpoint stops answering, `/healthz` included. So these tests
check the deadline is really attached, not that some wrapper exists.
"""

import socket
import threading
import time

from alphadesk.net import ALPACA_TIMEOUT_S, bound_timeout


class FakeSession:
    """Stands in for the requests.Session alpaca-py builds internally."""

    def __init__(self):
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append(kwargs)
        return "ok"


class FakeClient:
    def __init__(self):
        self._session = FakeSession()


class TestBoundTimeout:
    def test_a_timeout_is_injected(self):
        c = bound_timeout(FakeClient())
        c._session.request("GET", "/x")
        assert c._session.calls[0]["timeout"] == ALPACA_TIMEOUT_S

    def test_an_explicit_timeout_is_respected(self):
        """setdefault, not overwrite — an SDK call that sets its own deadline
        knows something we don't."""
        c = bound_timeout(FakeClient())
        c._session.request("GET", "/x", timeout=0.25)
        assert c._session.calls[0]["timeout"] == 0.25

    def test_the_configured_value_is_used(self):
        c = bound_timeout(FakeClient(), timeout_s=7)
        c._session.request("GET", "/x")
        assert c._session.calls[0]["timeout"] == 7

    def test_wrapping_twice_does_not_nest(self):
        """Nested closures would make the effective deadline unknowable."""
        c = FakeClient()
        first = bound_timeout(c, timeout_s=5)._session.request
        second = bound_timeout(c, timeout_s=9)._session.request
        assert first is second
        c._session.request("GET", "/x")
        assert c._session.calls[0]["timeout"] == 5, "the first wrap stands"

    def test_a_client_without_a_session_degrades(self):
        """A future SDK could rename the attribute. Losing the guarantee is
        bad; crashing the terminal over it is worse."""
        class Renamed:
            pass

        obj = Renamed()
        assert bound_timeout(obj) is obj

    def test_missing_session_is_logged(self, caplog):
        import logging

        class Renamed:
            pass

        with caplog.at_level(logging.WARNING, logger="alphadesk.net"):
            bound_timeout(Renamed())
        # It must not go silently: the symptom would be a hang months later,
        # with nothing in the logs pointing back at the lost guarantee.
        assert any("unbounded" in r.getMessage() for r in caplog.records)


class TestAgainstARealSocket:
    """The end-to-end case: a server that accepts and then says nothing."""

    def test_a_black_hole_upstream_returns_instead_of_hanging(self):
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(5)
        held = []

        def blackhole():
            while True:
                try:
                    held.append(srv.accept()[0])   # hold open, never respond
                except OSError:
                    return

        threading.Thread(target=blackhole, daemon=True).start()
        try:
            import requests

            class RealSessionClient:
                def __init__(self):
                    self._session = requests.Session()

            c = bound_timeout(RealSessionClient(), timeout_s=1)
            started = time.monotonic()
            try:
                c._session.request("GET", f"http://127.0.0.1:{srv.getsockname()[1]}/x")
            except requests.exceptions.Timeout:
                elapsed = time.monotonic() - started
            else:
                raise AssertionError("the black hole answered, which it must not")
            assert elapsed < 5, f"took {elapsed:.1f}s — the deadline did not apply"
        finally:
            srv.close()
            for conn in held:
                conn.close()
