"""Tests for the polite HTTP client. Every test uses httpx.MockTransport: no network."""

from __future__ import annotations

import json
from datetime import date

import httpx
import pytest

from etfp.http import (
    DEFAULT_USER_AGENT,
    PoliteClient,
    SourceBlockedError,
    SourceError,
    SourceNotFoundError,
    SourceUnavailableError,
    is_connection_failure,
)
from etfp.timeutil import IST

DAY = date(2026, 11, 11)


class FakeClock:
    """Time that only moves when someone sleeps. Lets tests run instantly."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class Recorder:
    """A mock transport that records requests and replays scripted responses."""

    def __init__(self, clock: FakeClock, script: list | None = None, default=None) -> None:
        self.clock = clock
        self.script = list(script or [])
        self.default = default if default is not None else httpx.Response(200, content=b"ok")
        self.requests: list[httpx.Request] = []
        self.times: list[float] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        self.times.append(self.clock.now)
        item = self.script.pop(0) if self.script else self.default
        if isinstance(item, Exception):
            raise item
        return item


def make_client(clock: FakeClock, rec: Recorder, **kwargs) -> PoliteClient:
    kwargs.setdefault("rng", lambda: 0.5)
    return PoliteClient(
        transport=httpx.MockTransport(rec),
        sleep=clock.sleep,
        monotonic=clock.monotonic,
        **kwargs,
    )


def test_success_returns_exact_bytes_and_ist_timestamp() -> None:
    clock = FakeClock()
    body = b"a,b\r\n1,2\r\n\x00\xff"
    rec = Recorder(clock, [httpx.Response(200, content=body, headers={"X-Test": "1"})])
    with make_client(clock, rec) as client:
        res = client.get("https://www.nseindia.com/x.csv", source="nse_test", day=DAY)
    assert res.content == body
    assert res.status_code == 200
    assert res.url == "https://www.nseindia.com/x.csv"
    assert res.headers["x-test"] == "1"
    assert res.fetched_at.tzinfo is not None
    assert res.fetched_at.utcoffset() == IST.utcoffset(res.fetched_at)
    assert res.fetched_at.utcoffset().total_seconds() == 5.5 * 3600


def test_nse_hosts_share_one_timer_and_other_hosts_are_not_delayed() -> None:
    clock = FakeClock()
    rec = Recorder(clock)
    with make_client(clock, rec) as client:
        client.get("https://www.nseindia.com/a", source="s", day=DAY)
        client.get("https://nsearchives.nseindia.com/b", source="s", day=DAY)
        assert rec.times[1] - rec.times[0] >= 1.5
        before = clock.now
        client.get("https://www.amfiindia.com/c", source="s", day=DAY)
        # The unrelated host was never requested before: no wait at all.
        assert clock.now == before
        assert rec.times[2] == before
    assert clock.sleeps == [pytest.approx(1.5)]


def test_unmatched_host_uses_default_interval_per_host() -> None:
    clock = FakeClock()
    rec = Recorder(clock)
    with make_client(clock, rec, default_interval_s=0.5) as client:
        client.get("https://one.test/a", source="s", day=DAY)
        client.get("https://one.test/b", source="s", day=DAY)
        client.get("https://two.test/c", source="s", day=DAY)  # different host: own timer
    assert rec.times[1] - rec.times[0] >= 0.5
    assert rec.times[2] == rec.times[1]


def test_no_extra_wait_when_enough_time_already_passed() -> None:
    clock = FakeClock()
    rec = Recorder(clock)
    with make_client(clock, rec) as client:
        client.get("https://www.nseindia.com/a", source="s", day=DAY)
        clock.now += 10
        client.get("https://www.nseindia.com/b", source="s", day=DAY)
    assert clock.sleeps == []


@pytest.mark.parametrize("status", [403, 429, 401])
def test_blocks_raise_immediately_without_retry(status: int) -> None:
    clock = FakeClock()
    rec = Recorder(clock, default=httpx.Response(status))
    with make_client(clock, rec) as client, pytest.raises(SourceBlockedError) as info:
        client.get("https://www.nseindia.com/api/q", source="nse_quote", day=DAY)
    assert len(rec.requests) == 1
    assert clock.sleeps == []  # no backoff either
    msg = str(info.value)
    assert "nse_quote" in msg
    assert "2026-11-11" in msg
    assert f"HTTP {status}" in msg
    assert "https://www.nseindia.com/api/q" in msg
    assert info.value.source == "nse_quote"
    assert info.value.day == DAY
    assert info.value.status_code == status
    assert info.value.url == "https://www.nseindia.com/api/q"


def test_404_raises_not_found_after_one_request() -> None:
    clock = FakeClock()
    rec = Recorder(clock, default=httpx.Response(404))
    with make_client(clock, rec) as client, pytest.raises(SourceNotFoundError) as info:
        client.get("https://nsearchives.nseindia.com/f.csv", source="bhav", day=DAY)
    assert len(rec.requests) == 1
    assert "bhav" in str(info.value)
    assert "2026-11-11" in str(info.value)


def test_other_4xx_raises_source_error_without_retry() -> None:
    clock = FakeClock()
    rec = Recorder(clock, default=httpx.Response(400))
    with make_client(clock, rec) as client, pytest.raises(SourceError) as info:
        client.get("https://x.test/", source="s")
    assert type(info.value) is SourceError
    assert len(rec.requests) == 1
    assert "no date" in str(info.value)


def test_500_then_200_retries_once_with_backoff() -> None:
    clock = FakeClock()
    rec = Recorder(clock, [httpx.Response(500), httpx.Response(200, content=b"fine")])
    with make_client(clock, rec, backoff_base_s=2.0, rng=lambda: 0.5) as client:
        res = client.get("https://x.test/", source="s", day=DAY)
    assert res.content == b"fine"
    assert len(rec.requests) == 2
    # wait = base * 2**0 + rng * base = 2 + 1; the per-host gap is already satisfied by it
    assert any(s == pytest.approx(3.0) for s in clock.sleeps)


def test_backoff_grows_exponentially() -> None:
    clock = FakeClock()
    rec = Recorder(clock, default=httpx.Response(503))
    with (
        make_client(clock, rec, backoff_base_s=2.0, rng=lambda: 0.0) as client,
        pytest.raises(SourceUnavailableError),
    ):
        client.get("https://x.test/", source="s", day=DAY)
    assert [s for s in clock.sleeps if s >= 2.0] == [2.0, 4.0, 8.0]


def test_500_forever_raises_unavailable_after_all_attempts() -> None:
    clock = FakeClock()
    rec = Recorder(clock, default=httpx.Response(500))
    with (
        make_client(clock, rec, max_retries=3) as client,
        pytest.raises(SourceUnavailableError) as info,
    ):
        client.get("https://x.test/f", source="bhav", day=DAY)
    assert len(rec.requests) == 4  # first try + 3 retries
    msg = str(info.value)
    assert "bhav" in msg
    assert "2026-11-11" in msg
    assert "HTTP 500" in msg
    assert "https://x.test/f" in msg
    assert info.value.status_code == 500


def test_connect_error_is_retried_and_chained() -> None:
    clock = FakeClock()
    err = httpx.ConnectError("boom")
    rec = Recorder(clock, [err, httpx.Response(200, content=b"back")])
    with make_client(clock, rec) as client:
        assert client.get("https://x.test/", source="s", day=DAY).content == b"back"
    assert len(rec.requests) == 2

    clock2 = FakeClock()
    rec2 = Recorder(clock2, default=None)
    rec2.script = [httpx.ConnectError("a")] * 10
    with (
        make_client(clock2, rec2, max_retries=2) as client,
        pytest.raises(SourceUnavailableError) as info,
    ):
        client.get("https://x.test/", source="s", day=DAY)
    assert isinstance(info.value.__cause__, httpx.ConnectError)
    assert len(rec2.requests) == 3


def test_timeout_is_retried() -> None:
    clock = FakeClock()
    rec = Recorder(clock, [httpx.ReadTimeout("slow"), httpx.Response(200)])
    with make_client(clock, rec) as client:
        assert client.get("https://x.test/", source="s", day=DAY).status_code == 200
    assert len(rec.requests) == 2


def test_is_connection_failure_finds_the_cause_of_a_wrapped_error() -> None:
    """A laptop with no network gives ConnectError; PoliteClient wraps it after retries."""
    clock = FakeClock()
    rec = Recorder(clock, [httpx.ConnectError("no network")] * 2)
    with make_client(clock, rec, max_retries=1) as client, pytest.raises(SourceError) as info:
        client.get("https://x.test/", source="s", day=DAY)
    assert is_connection_failure(info.value)
    assert is_connection_failure(httpx.ConnectTimeout("slow to connect"))
    # The server was reached in these cases, so the network itself works:
    assert not is_connection_failure(httpx.ReadTimeout("slow answer"))
    assert not is_connection_failure(SourceError("HTTP 500", source="s", day=DAY, url="u"))
    assert not is_connection_failure(KeyError("parse error"))


def test_retries_also_respect_the_rate_limit() -> None:
    clock = FakeClock()
    rec = Recorder(clock, [httpx.Response(500), httpx.Response(200)])
    with make_client(clock, rec, backoff_base_s=0.0, rng=lambda: 0.0) as client:
        client.get("https://www.nseindia.com/a", source="s", day=DAY)
    assert rec.times[1] - rec.times[0] >= 1.5


def test_cookie_from_one_response_is_sent_on_next_request() -> None:
    clock = FakeClock()
    rec = Recorder(
        clock,
        [
            httpx.Response(200, headers={"Set-Cookie": "nsit=SECRETVALUE123; Path=/"}),
            httpx.Response(200),
        ],
    )
    with make_client(clock, rec) as client:
        client.get("https://www.nseindia.com/", source="s", day=DAY)
        client.get("https://www.nseindia.com/api/x", source="s", day=DAY)
    assert "cookie" not in rec.requests[0].headers
    assert "nsit=SECRETVALUE123" in rec.requests[1].headers["cookie"]


def test_headers_params_and_default_user_agent_are_sent() -> None:
    clock = FakeClock()
    rec = Recorder(clock)
    with make_client(clock, rec) as client:
        client.get(
            "https://x.test/api",
            source="s",
            day=DAY,
            headers={"Referer": "https://x.test/page", "Accept-Language": "hi"},
            params={"symbol": "ABC"},
        )
    req = rec.requests[0]
    assert req.headers["user-agent"] == DEFAULT_USER_AGENT
    assert "Chrome" in DEFAULT_USER_AGENT
    assert req.headers["referer"] == "https://x.test/page"
    assert req.headers["accept-language"] == "hi"  # caller overrides the default
    assert req.url.params["symbol"] == "ABC"


def test_default_accept_language_and_custom_user_agent() -> None:
    clock = FakeClock()
    rec = Recorder(clock)
    with make_client(clock, rec, user_agent="TestUA/1.0") as client:
        client.get("https://x.test/", source="s", day=DAY)
    assert rec.requests[0].headers["accept-language"] == "en-US,en;q=0.9"
    assert rec.requests[0].headers["user-agent"] == "TestUA/1.0"


def test_error_text_and_logs_do_not_contain_cookie_values(
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = FakeClock()
    rec = Recorder(
        clock,
        [
            httpx.Response(200, headers={"Set-Cookie": "nsit=SECRETVALUE123; Path=/"}),
            httpx.Response(403),
        ],
    )
    with caplog.at_level("DEBUG", logger="etfp.http"), make_client(clock, rec) as client:
        client.get("https://www.nseindia.com/", source="s", day=DAY)
        with pytest.raises(SourceBlockedError) as info:
            client.get(
                "https://www.nseindia.com/api/x",
                source="nse_quote",
                day=DAY,
                headers={"X-Token": "TOPSECRET"},
            )
    assert "SECRETVALUE123" not in str(info.value)
    assert "TOPSECRET" not in str(info.value)
    assert "SECRETVALUE123" not in caplog.text
    assert "TOPSECRET" not in caplog.text
    assert any(r.levelname == "ERROR" for r in caplog.records)
    assert any(r.levelname == "INFO" for r in caplog.records)


def test_retry_logs_warning(caplog: pytest.LogCaptureFixture) -> None:
    clock = FakeClock()
    rec = Recorder(clock, [httpx.Response(502), httpx.Response(200)])
    with caplog.at_level("INFO", logger="etfp.http"), make_client(clock, rec) as client:
        client.get("https://x.test/", source="s", day=DAY)
    assert [r.levelname for r in caplog.records].count("WARNING") == 1


def test_redirect_followed_and_final_url_reported() -> None:
    clock = FakeClock()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/old":
            return httpx.Response(302, headers={"Location": "https://x.test/new"})
        return httpx.Response(200, content=b"new")

    with PoliteClient(
        transport=httpx.MockTransport(handler), sleep=clock.sleep, monotonic=clock.monotonic
    ) as client:
        res = client.get("https://x.test/old", source="s", day=DAY)
    assert res.url == "https://x.test/new"
    assert res.content == b"new"


# -- POST ----------------------------------------------------------------


def test_post_sends_method_json_body_and_content_type() -> None:
    clock = FakeClock()
    rec = Recorder(clock, [httpx.Response(200, content=b'{"ok":1}')])
    with make_client(clock, rec) as client:
        res = client.post(
            "https://one.test/api",
            source="s",
            day=DAY,
            json={"apiName": "X"},
            headers={"Origin": "https://one.test"},
        )
    assert res.content == b'{"ok":1}'
    request = rec.requests[0]
    assert request.method == "POST"
    assert json.loads(request.content) == {"apiName": "X"}
    assert request.headers["content-type"] == "application/json"
    assert request.headers["origin"] == "https://one.test"


def test_post_with_no_body_sends_content_length_zero() -> None:
    clock = FakeClock()
    rec = Recorder(clock)
    with make_client(clock, rec) as client:
        client.post("https://one.test/api", source="s", day=DAY)
    request = rec.requests[0]
    assert request.method == "POST"
    assert request.content == b""
    assert request.headers["content-length"] == "0"


def test_post_with_raw_content_sends_those_bytes() -> None:
    clock = FakeClock()
    rec = Recorder(clock)
    with make_client(clock, rec) as client:
        client.post("https://one.test/api", source="s", day=DAY, content=b"abc")
    assert rec.requests[0].content == b"abc"
    assert rec.requests[0].headers["content-length"] == "3"


def test_post_403_raises_blocked_after_one_request() -> None:
    clock = FakeClock()
    rec = Recorder(clock, default=httpx.Response(403))
    with make_client(clock, rec) as client, pytest.raises(SourceBlockedError) as info:
        client.post("https://one.test/api", source="amc_x", day=DAY, json={})
    assert len(rec.requests) == 1
    assert clock.sleeps == []
    assert "amc_x" in str(info.value)
    assert "2026-11-11" in str(info.value)


def test_post_500_then_200_retries() -> None:
    clock = FakeClock()
    rec = Recorder(clock, [httpx.Response(500), httpx.Response(200, content=b"fine")])
    with make_client(clock, rec) as client:
        res = client.post("https://one.test/api", source="s", day=DAY, json={"a": 1})
    assert res.content == b"fine"
    assert len(rec.requests) == 2
    assert [r.method for r in rec.requests] == ["POST", "POST"]
    assert json.loads(rec.requests[1].content) == {"a": 1}  # body re-sent on the retry


def test_get_and_post_share_the_rate_limit_for_a_host() -> None:
    clock = FakeClock()
    rec = Recorder(clock)
    with make_client(clock, rec, default_interval_s=0.5) as client:
        client.get("https://one.test/a", source="s", day=DAY)
        client.post("https://one.test/b", source="s", day=DAY)
    assert rec.times[1] - rec.times[0] >= 0.5


def test_post_with_both_json_and_content_is_a_value_error() -> None:
    clock = FakeClock()
    rec = Recorder(clock)
    with make_client(clock, rec) as client, pytest.raises(ValueError, match="not both"):
        client.post("https://one.test/api", source="s", day=DAY, json={}, content=b"x")
    assert rec.requests == []
