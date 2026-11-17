"""Tests for the connectivity probe run before the daily downloads (etfp.netcheck).

No real network: DNS is a fake function and HTTP answers come from httpx.MockTransport.
"""

from __future__ import annotations

import socket

import httpx

from etfp import netcheck

URLS = ("https://one.test/", "https://two.test/", "https://three.test/")


def dns_ok(host: str, port: int) -> list[object]:
    return [("fake", host, port)]


def dns_down(host: str, port: int) -> list[object]:
    raise socket.gaierror(8, "nodename nor servname provided, or not known")


def test_first_site_answering_means_online_and_only_one_request_is_sent() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200)

    result = netcheck.probe(URLS, transport=httpx.MockTransport(handler), resolve=dns_ok)
    assert result.online
    assert seen == ["https://one.test/"]
    assert [c.detail for c in result.checks] == ["HTTP 200"]


def test_an_http_error_code_still_proves_the_network_works() -> None:
    result = netcheck.probe(
        URLS, transport=httpx.MockTransport(lambda r: httpx.Response(403)), resolve=dns_ok
    )
    assert result.online and result.checks[0].detail == "HTTP 403"


def test_no_dns_anywhere_is_offline_and_no_request_is_sent() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200)

    result = netcheck.probe(URLS, transport=httpx.MockTransport(handler), resolve=dns_down)
    assert not result.online
    assert seen == []
    assert len(result.checks) == 3
    assert all("DNS lookup failed" in c.detail for c in result.checks)
    assert "one.test: DNS lookup failed" in result.summary()
    assert result.as_data()[0]["ok"] is False


def test_connection_errors_on_every_site_is_offline_but_one_good_site_is_enough() -> None:
    def all_down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host", request=request)

    down = netcheck.probe(URLS, transport=httpx.MockTransport(all_down), resolve=dns_ok)
    assert not down.online
    assert [c.detail for c in down.checks] == ["no answer (ConnectError)"] * 3

    def third_up(request: httpx.Request) -> httpx.Response:
        if request.url.host == "three.test":
            return httpx.Response(301)
        raise httpx.ConnectTimeout("timed out", request=request)

    up = netcheck.probe(URLS, transport=httpx.MockTransport(third_up), resolve=dns_ok)
    assert up.online
    assert [c.ok for c in up.checks] == [False, False, True]


def test_default_probe_sites_are_independent_and_not_nse() -> None:
    hosts = [httpx.URL(u).host for u in netcheck.PROBE_URLS]
    assert len(set(hosts)) == len(hosts) >= 2
    assert not any("nseindia" in h for h in hosts)
