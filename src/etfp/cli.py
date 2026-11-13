"""Command-line entry points. Sub-commands are added milestone by milestone.

Each command is a thin wrapper: the real work lives in ``etfp.recorder`` and
``etfp.ops`` so it can be tested without a terminal.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from datetime import datetime

import typer

from etfp import __version__, calendars, ops, paths, recorder
from etfp.config import CollectSettings, load_collect_settings
from etfp.http import PoliteClient
from etfp.rawstore import RawStore
from etfp.timeutil import IST, now_ist, today_ist

app = typer.Typer(
    help="ETF premium tracker: collect raw data, build the clean table, export.",
    no_args_is_help=True,
)
collect_app = typer.Typer(help="Download raw data (saved untouched under data/raw/).")
ops_app = typer.Typer(help="Scheduling and operations helpers.")
app.add_typer(collect_app, name="collect")
app.add_typer(ops_app, name="ops")

EXIT_USAGE = 2
EXIT_LOCKED = 3
_LOG_MAX_BYTES = 5 * 1024 * 1024
_LOG_BACKUPS = 5
_HANDLER_MARK = "_etfp_cli_handler"


class _IstFormatter(logging.Formatter):
    """Log timestamps in ISO format with the India offset, e.g. 2026-11-11T09:15:02+05:30."""

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        return datetime.fromtimestamp(record.created, tz=IST).isoformat(timespec="seconds")


def _configure_logging() -> None:
    """Send INFO logs to stderr and to a rotating file logs/collect.log (5 MB x 5)."""
    logger = logging.getLogger("etfp")
    for old in [h for h in logger.handlers if getattr(h, _HANDLER_MARK, False)]:
        logger.removeHandler(old)  # configure once, even if called twice
        old.close()
    formatter = _IstFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    try:
        log_dir = paths.logs_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        handlers.append(
            logging.handlers.RotatingFileHandler(
                log_dir / "collect.log",
                maxBytes=_LOG_MAX_BYTES,
                backupCount=_LOG_BACKUPS,
                encoding="utf-8",
            )
        )
    except OSError as exc:
        print(
            f"warning: cannot write logs/collect.log ({exc}); logging to stderr only",
            file=sys.stderr,
        )
    for handler in handlers:
        handler.setFormatter(formatter)
        setattr(handler, _HANDLER_MARK, True)
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)


def _build_client(settings: CollectSettings) -> PoliteClient:
    return PoliteClient(
        min_interval_s={"nseindia.com": settings.http.nse_min_interval_s},
        default_interval_s=settings.http.default_interval_s,
        max_retries=settings.http.max_retries,
    )


@app.command()
def version() -> None:
    """Print the package version."""
    typer.echo(__version__)


@collect_app.command("inav")
def collect_inav(
    once: bool = typer.Option(False, "--once", help="Take one snapshot of every source now."),
    loop: bool = typer.Option(False, "--loop", help="Record today's whole 15-minute schedule."),
    force: bool = typer.Option(False, "--force", help="Run even if NSE is closed today."),
) -> None:
    """Record iNAV snapshots (fund-house pages and the NSE ETF table)."""
    if once == loop:
        typer.echo("error: give exactly one of --once or --loop", err=True)
        raise typer.Exit(EXIT_USAGE)
    _configure_logging()
    settings = load_collect_settings()
    store = RawStore(paths.raw_dir())
    with _build_client(settings) as client:
        if once:
            _inav_once(client, store, force)
        else:
            _inav_loop(client, store, settings, force)


def _inav_once(client: PoliteClient, store: RawStore, force: bool) -> None:
    today = today_ist()
    if not force and not calendars.is_nse_session(today):
        typer.echo(
            f"NSE is closed today ({today.isoformat()}); nothing to record. "
            "Use --force to take a snapshot anyway.",
            err=True,
        )
        raise typer.Exit(EXIT_USAGE)
    result = recorder.run_tick(client, store)
    for outcome in result.outcomes:
        if outcome.ok:
            typer.echo(f"OK {outcome.relpath} {outcome.size_bytes}")
        else:
            typer.echo(f"FAILED {outcome.error_type}: {outcome.error}")
    if result.ok_count == 0:
        raise typer.Exit(1)


def _inav_loop(
    client: PoliteClient, store: RawStore, settings: CollectSettings, force: bool
) -> None:
    try:
        results = recorder.run_loop(client=client, store=store, settings=settings, force=force)
    except recorder.RecorderAlreadyRunningError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(EXIT_LOCKED) from None
    typer.echo(f"recorder finished: {len(results)} ticks run")


@app.command()
def status() -> None:
    """Show the recorder's last heartbeat, today's failures and today's raw file counts."""
    store = RawStore(paths.raw_dir())
    for line in ops.status_lines(store=store, state_dir=paths.state_dir(), now=now_ist()):
        typer.echo(line)


@ops_app.command("launchd-plist")
def launchd_plist() -> None:
    """Print the macOS launchd job that starts the recorder (installs nothing)."""
    try:
        uv_path = ops.find_uv()
    except FileNotFoundError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(EXIT_USAGE) from None
    typer.echo(ops.render_inav_plist(project_root=paths.project_root(), uv_path=uv_path), nl=False)


if __name__ == "__main__":
    app()
