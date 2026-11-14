"""Send a text message to Telegram, and read the bot's secrets from ``.env``.

Secrets (CLAUDE.md): the bot token and chat id live in ``.env`` (gitignored) or in the process
environment, never in code, logs or error messages. The token is part of Telegram's web address
(``https://api.telegram.org/bot<TOKEN>/sendMessage``), so any library that prints the address
would print the token. This module therefore:

* turns every failure into ``TelegramError`` whose text is scrubbed of the token, and raises it
  with ``from None`` so the original (token-carrying) exception is not shown in a traceback;
* switches off the HTTP library's own request logging, which prints the full address.

Why not python-dotenv: it is not a project dependency, and the ``.env`` format we need is tiny.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from etfp import paths

logger = logging.getLogger("etfp.alerts.telegram")

TOKEN_VAR = "TELEGRAM_BOT_TOKEN"
CHAT_VAR = "TELEGRAM_CHAT_ID"
API_BASE = "https://api.telegram.org"
MAX_MESSAGE_CHARS = 4096  # Telegram's hard limit for one message
REQUEST_TIMEOUT_S = 15.0


class TelegramError(RuntimeError):
    """Sending failed. The text never contains the bot token."""


class MissingCredentialsError(TelegramError):
    """The token or chat id is not set. The text names the variables, not any value."""


# --------------------------------------------------------------------------
# Credentials
# --------------------------------------------------------------------------
def parse_env_file(text: str) -> dict[str, str]:
    """Read ``KEY=VALUE`` lines. Understands comments, ``export``, and single/double quotes."""
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        elif " #" in value:  # trailing comment on an unquoted value
            value = value.split(" #", 1)[0].rstrip()
        if key:
            values[key] = value
    return values


@dataclass(frozen=True)
class Credentials:
    """Bot token and chat id. ``repr`` leaves the token out so it cannot leak through a log line."""

    token: str = field(repr=False)
    chat_id: str


def load_credentials(
    environ: Mapping[str, str] | None = None, env_file: Path | None = None
) -> Credentials:
    """Token and chat id from the environment first, then from ``<project>/.env``.

    Raises ``MissingCredentialsError`` naming which variable is missing (never a value).
    """
    env = os.environ if environ is None else environ
    file_values: dict[str, str] = {}
    path = env_file if env_file is not None else paths.project_root() / ".env"
    try:
        file_values = parse_env_file(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise MissingCredentialsError(f"cannot read {path}: {type(exc).__name__}") from None

    def pick(name: str) -> str:
        return (env.get(name) or file_values.get(name) or "").strip()

    token, chat_id = pick(TOKEN_VAR), pick(CHAT_VAR)
    missing = [n for n, v in ((TOKEN_VAR, token), (CHAT_VAR, chat_id)) if not v]
    if missing:
        raise MissingCredentialsError(
            f"missing {' and '.join(missing)}: set it in the environment or in {path} "
            "(see .env.example). Use `--dry-run` to print alerts without sending."
        )
    return Credentials(token=token, chat_id=chat_id)


# --------------------------------------------------------------------------
# Sending
# --------------------------------------------------------------------------
def _quiet_http_logs() -> None:
    """httpx logs every request URL at INFO level; that URL contains the token."""
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)


class TelegramSender:
    """Posts messages with the Bot API ``sendMessage`` call.

    Pass ``client`` (for example one built on ``httpx.MockTransport``) in tests so no network
    is touched. If the sender creates the client itself, ``close()`` closes it.
    """

    def __init__(self, credentials: Credentials, client: httpx.Client | None = None) -> None:
        _quiet_http_logs()
        self._credentials = credentials
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=REQUEST_TIMEOUT_S)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> TelegramSender:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _scrub(self, text: str) -> str:
        return text.replace(self._credentials.token, "<token>")

    def send(self, text: str) -> None:
        """Send one message. Raises ``TelegramError`` on any failure (never returns quietly)."""
        if not text.strip():
            raise TelegramError("refusing to send an empty message")
        if len(text) > MAX_MESSAGE_CHARS:
            raise TelegramError(
                f"message is {len(text)} characters; Telegram allows {MAX_MESSAGE_CHARS}"
            )
        url = f"{API_BASE}/bot{self._credentials.token}/sendMessage"
        payload = {
            "chat_id": self._credentials.chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        try:
            response = self._client.post(url, json=payload)
        except httpx.HTTPError as exc:
            raise TelegramError(
                f"could not reach Telegram ({type(exc).__name__}); nothing was sent"
            ) from None

        detail = ""
        try:
            body = response.json()
        except ValueError:
            body = None
        if isinstance(body, dict):
            detail = self._scrub(str(body.get("description") or ""))
        if response.status_code != 200 or not (isinstance(body, dict) and body.get("ok") is True):
            reason = f": {detail}" if detail else ""
            raise TelegramError(
                f"Telegram refused the message (HTTP {response.status_code}){reason}"
            )
        logger.info("telegram message sent (%d characters)", len(text))
