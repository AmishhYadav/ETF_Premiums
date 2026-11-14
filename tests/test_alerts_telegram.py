"""Tests for the Telegram sender. No network: httpx.MockTransport answers every request."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx
import pytest

from etfp.alerts.telegram import (
    MAX_MESSAGE_CHARS,
    Credentials,
    MissingCredentialsError,
    TelegramError,
    TelegramSender,
    load_credentials,
    parse_env_file,
)

TOKEN = "123456:SECRET-token_abc"
CHAT = "-100777"


def sender_with(handler) -> TelegramSender:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return TelegramSender(Credentials(token=TOKEN, chat_id=CHAT), client=client)


# -- .env reading ----------------------------------------------------------------------------
def test_parse_env_file_formats() -> None:
    text = """
# comment
TELEGRAM_BOT_TOKEN=abc:def
export TELEGRAM_CHAT_ID = "42"
QUOTED='single quoted'
TRAILING=value # a note
EMPTY=
no_equals_line
"""
    assert parse_env_file(text) == {
        "TELEGRAM_BOT_TOKEN": "abc:def",
        "TELEGRAM_CHAT_ID": "42",
        "QUOTED": "single quoted",
        "TRAILING": "value",
        "EMPTY": "",
    }


def test_credentials_environment_beats_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("TELEGRAM_BOT_TOKEN=from_file\nTELEGRAM_CHAT_ID=1\n")
    got = load_credentials({"TELEGRAM_BOT_TOKEN": "from_env"}, env_file)
    assert (got.token, got.chat_id) == ("from_env", "1")


def test_credentials_from_file_only(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("TELEGRAM_BOT_TOKEN=t\nTELEGRAM_CHAT_ID=9\n")
    assert load_credentials({}, env_file).chat_id == "9"


def test_missing_credentials_names_variables_not_values(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("TELEGRAM_BOT_TOKEN=supersecret\n")
    with pytest.raises(MissingCredentialsError) as err:
        load_credentials({}, env_file)
    assert "TELEGRAM_CHAT_ID" in str(err.value) and "supersecret" not in str(err.value)
    with pytest.raises(MissingCredentialsError, match="TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID"):
        load_credentials({}, tmp_path / "does_not_exist.env")


def test_credentials_repr_hides_token() -> None:
    assert TOKEN not in repr(Credentials(token=TOKEN, chat_id=CHAT))


# -- sending ---------------------------------------------------------------------------------
def test_send_posts_expected_request() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    with sender_with(handler) as sender:
        sender.send("hello")
    (req,) = seen
    assert req.method == "POST"
    assert str(req.url) == f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    body = json.loads(req.content)
    assert body == {"chat_id": CHAT, "text": "hello", "disable_web_page_preview": True}


def test_http_error_raises_without_token_even_if_telegram_echoes_it() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401, json={"ok": False, "description": f"Unauthorized (bad token {TOKEN})"}
        )

    with pytest.raises(TelegramError) as err:
        sender_with(handler).send("hi")
    text = str(err.value)
    assert "HTTP 401" in text and "Unauthorized" in text
    assert TOKEN not in text and "SECRET" not in text


def test_ok_false_with_status_200_is_still_a_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": False, "description": "odd"})

    with pytest.raises(TelegramError, match="odd"):
        sender_with(handler).send("hi")


def test_non_json_error_page_is_a_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="<html>bad gateway</html>")

    with pytest.raises(TelegramError, match="HTTP 502"):
        sender_with(handler).send("hi")


def test_network_error_is_wrapped_and_hides_original_exception() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"failed to reach {request.url}")  # message carries the token

    with pytest.raises(TelegramError) as err:
        sender_with(handler).send("hi")
    assert "ConnectError" in str(err.value) and TOKEN not in str(err.value)
    assert err.value.__cause__ is None and err.value.__suppress_context__


def test_empty_and_oversized_messages_are_refused_before_any_request() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, json={"ok": True})

    sender = sender_with(handler)
    with pytest.raises(TelegramError, match="empty"):
        sender.send("   ")
    with pytest.raises(TelegramError, match="characters"):
        sender.send("x" * (MAX_MESSAGE_CHARS + 1))
    sender.send("x" * MAX_MESSAGE_CHARS)  # exactly at the limit is allowed
    assert calls == [1]


def test_sender_silences_httpx_request_logging() -> None:
    logging.getLogger("httpx").setLevel(logging.INFO)  # what a verbose app might do
    sender_with(lambda r: httpx.Response(200, json={"ok": True}))
    assert logging.getLogger("httpx").level == logging.WARNING
