"""Command-line entry points. Sub-commands are added milestone by milestone.

Each command is a thin wrapper: the real work lives in ``etfp.recorder``,
``etfp.ops`` and ``etfp.collect.jobs`` so it can be tested without a terminal.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from datetime import date, datetime
from typing import Annotated

import typer

from etfp import __version__, calendars, ops, paths, recorder
from etfp.collect import jobs
from etfp.collect.backfill_state import STATE_FILE_NAME, BackfillState
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
export_app = typer.Typer(help="Write exports that other tools read (research handoff).")
run_app = typer.Typer(help="Scheduled pipeline runs (what launchd starts).", no_args_is_help=True)
app.add_typer(collect_app, name="collect")
app.add_typer(ops_app, name="ops")
app.add_typer(export_app, name="export")
app.add_typer(run_app, name="run")

EXIT_USAGE = 2
EXIT_LOCKED = 3  # another recorder / another `run daily` holds the lock
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


# AMFI builds history files on request; a 7-day window (~6 MB) can take well over the
# 30 s default, so the daily/backfill client waits longer before giving up on a read.
_BULK_TIMEOUT_S = 120.0


def _build_bulk_client(settings: CollectSettings) -> PoliteClient:
    return PoliteClient(
        min_interval_s={"nseindia.com": settings.http.nse_min_interval_s},
        default_interval_s=settings.http.default_interval_s,
        max_retries=settings.http.max_retries,
        timeout_s=_BULK_TIMEOUT_S,
    )


def _parse_day(text: str, option: str) -> date:
    """Strict YYYY-MM-DD; anything else is a usage error that names the option."""
    try:
        return date.fromisoformat(text)
    except ValueError:
        typer.echo(f"error: {option} must be a date like 2026-11-09, got {text!r}", err=True)
        raise typer.Exit(EXIT_USAGE) from None


def _source_names(values: list[str] | None, allowed: tuple[str, ...]) -> list[str]:
    try:
        return jobs.parse_sources(values, allowed=allowed)
    except ValueError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(EXIT_USAGE) from None


def _print_outcome(outcome: jobs.Outcome, *, quiet_skips: bool) -> None:
    """One line per download. Failures go to stderr so cron mails them."""
    unit = outcome.unit
    where = f"{unit.source} {unit.day.isoformat()}"
    if outcome.status == "fetched":
        typer.echo(f"OK          {where}: {outcome.detail}")
    elif outcome.status == "failed":
        typer.echo(f"FAILED      {where}: {outcome.detail}", err=True)
    elif outcome.status == "unavailable":
        if not quiet_skips or outcome.detail.startswith("HTTP 404"):
            typer.echo(f"UNAVAILABLE {where} {unit.name}: {outcome.detail}")
    elif not quiet_skips:
        typer.echo(f"EXISTS      {where} {unit.name}")


def _print_summary(outcomes: list[jobs.Outcome]) -> int:
    counts = jobs.summarize(outcomes)
    typer.echo(
        f"summary: {counts['fetched']} fetched, {counts['exists']} already stored, "
        f"{counts['unavailable']} not available, {counts['failed']} FAILED"
    )
    return counts["failed"]


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


@collect_app.command("nse-quote")
def collect_nse_quote(
    symbol: Annotated[
        list[str] | None,
        typer.Option(
            "--symbol", "-s", help="ETF symbol (repeat for several). Default: all tracked."
        ),
    ] = None,
    headed: Annotated[
        bool, typer.Option("--headed", help="Show the browser window (to see what NSE does).")
    ] = False,
    channel: Annotated[
        str | None, typer.Option("--channel", help='Browser channel, e.g. "chromium".')
    ] = None,
) -> None:
    """Take ONE NSE quote capture (price block + order book) through a real browser.

    Writes under data/raw/nse_quote/ exactly like the recorder does (it is real data,
    never overwritten). Use --headed to watch the browser, --channel chromium to try
    the full browser in its new headless mode.
    """
    from etfp.collect import nse_quote
    from etfp.config import load_etfs

    _configure_logging()
    cfg = load_collect_settings().nse_quote.model_copy(
        update={
            "headless": not headed,
            **({"channel": channel} if channel else {}),
        }
    )
    symbols = symbol or [e.symbol for e in load_etfs().tracked()]
    store = RawStore(paths.raw_dir())
    try:
        report = nse_quote.capture_symbols(store, symbols, cfg)
    except nse_quote.BrowserUnavailableError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from None
    for record in report.saved:
        typer.echo(f"OK {record.relpath} {record.size_bytes}")
    for problem in report.problems:
        typer.echo(f"PROBLEM {problem}", err=True)
    if not report.saved:
        raise typer.Exit(1)


@collect_app.command("daily")
def collect_daily(
    day_text: Annotated[
        str | None,
        typer.Option(
            "--date", help="Trading day to collect, YYYY-MM-DD (default: today in India)."
        ),
    ] = None,
    sources: Annotated[
        list[str] | None,
        typer.Option(
            "--sources",
            help="Only these sources (repeat or comma-separate). Default: all. "
            f"Choices: {', '.join(jobs.DAILY_SOURCES)}.",
        ),
    ] = None,
    force: Annotated[
        bool, typer.Option("--force", help="Download again even if the raw file is already stored.")
    ] = False,
) -> None:
    """Download every daily source for one NSE trading day (idempotent; safe to re-run).

    Skips days NSE was closed. Files already in data/raw are not fetched again. A source
    that fails is reported (source, date, reason) and the others still run; the exit code
    is 1 if anything failed.
    """
    chosen = _source_names(sources, jobs.DAILY_SOURCES)
    now = now_ist()
    today = now.date()
    day = _parse_day(day_text, "--date") if day_text else today
    if day > today:
        typer.echo(f"error: --date {day.isoformat()} is in the future (today is {today})", err=True)
        raise typer.Exit(EXIT_USAGE)
    try:
        open_today = calendars.is_nse_session(day)
    except ValueError as exc:  # before the sample start or beyond the calendar
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(EXIT_USAGE) from None
    if not open_today:
        typer.echo(
            f"NSE was closed on {day.isoformat()} ({day.strftime('%A')}); "
            "nothing to collect for that day."
        )
        return
    _configure_logging()
    plan = jobs.plan_daily(day, chosen, now=now)
    for note in plan.notes:
        typer.echo(f"NOTE        {note}")
    settings = load_collect_settings()
    store = RawStore(paths.raw_dir())
    with _build_bulk_client(settings) as client:
        outcomes = jobs.run_units(
            plan.units,
            client,
            store,
            force=force,
            on_outcome=lambda o: _print_outcome(o, quiet_skips=False),
        )
    if _print_summary(outcomes):
        raise typer.Exit(1)


@app.command("backfill")
def backfill(
    from_text: Annotated[str, typer.Option("--from", help="First day to collect, YYYY-MM-DD.")],
    to_text: Annotated[
        str | None,
        typer.Option("--to", help="Last day to collect, YYYY-MM-DD (default: today in India)."),
    ] = None,
    sources: Annotated[
        list[str] | None,
        typer.Option(
            "--sources",
            help="Only these sources (repeat or comma-separate). Default: all that can be "
            f"backfilled. Choices: {', '.join(jobs.BACKFILL_SOURCES)}.",
        ),
    ] = None,
    retry_unavailable: Annotated[
        bool,
        typer.Option(
            "--retry-unavailable",
            help=f"Ask again for files recorded as not available (data/state/{STATE_FILE_NAME}).",
        ),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Only count what would be downloaded; request nothing."),
    ] = False,
) -> None:
    """Download history for a date range. Resumable: stored files and known 404s are skipped.

    Polite by design: NSE requests are at least 1.5 s apart, AMFI history is requested in
    7-day windows, and a block (HTTP 403/429) stops all requests to that website.
    """
    chosen = _source_names(sources, jobs.BACKFILL_SOURCES)
    now = now_ist()
    start = _parse_day(from_text, "--from")
    end = _parse_day(to_text, "--to") if to_text else now.date()
    try:
        plan = jobs.plan_backfill(start, end, chosen, now=now)
    except ValueError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(EXIT_USAGE) from None
    for note in plan.notes:
        typer.echo(f"NOTE        {note}")
    store = RawStore(paths.raw_dir())
    state = BackfillState.load(paths.state_dir() / STATE_FILE_NAME)
    if dry_run:
        _print_dry_run(plan, store, state, retry_unavailable)
        return
    _configure_logging()
    settings = load_collect_settings()
    with _build_bulk_client(settings) as client:
        outcomes = jobs.run_units(
            plan.units,
            client,
            store,
            state=state,
            retry_unavailable=retry_unavailable,
            on_outcome=lambda o: _print_outcome(o, quiet_skips=True),
        )
    if _print_summary(outcomes):
        raise typer.Exit(1)


def _print_dry_run(
    plan: jobs.Plan, store: RawStore, state: BackfillState, retry_unavailable: bool
) -> None:
    todo: dict[str, int] = {}
    stored = known = 0
    for unit in plan.units:
        if store.exists(unit.source, unit.day, unit.name):
            stored += 1
        elif not retry_unavailable and state.is_unavailable(unit.source, unit.day, unit.name):
            known += 1
        else:
            todo[unit.source] = todo.get(unit.source, 0) + 1
    for source, count in sorted(todo.items()):
        typer.echo(f"would download {count:5d} x {source}")
    nse = sum(n for s, n in todo.items() if s.startswith("nse_"))
    typer.echo(
        f"dry run: {sum(todo.values())} to download, {stored} already stored, "
        f"{known} known not available; NSE requests alone take about {nse * 1.5 / 60:.0f} min"
    )


@app.command()
def status() -> None:
    """Show the recorder's last heartbeat, today's failures and today's raw file counts."""
    store = RawStore(paths.raw_dir())
    for line in ops.status_lines(store=store, state_dir=paths.state_dir(), now=now_ist()):
        typer.echo(line)


@app.command()
def build(
    no_export: Annotated[
        bool, typer.Option("--no-export", help="Skip the research export after the build.")
    ] = False,
) -> None:
    """Rebuild every clean table and etf_day from data/raw, then export for research.

    CLAUDE.md asks for the research export after every build, so it runs by
    default. A bad raw file stops the build with the table and file named.
    """
    # Imported here so `etfp collect ...` stays fast and does not load pandas-heavy code.
    from etfp.build import build_all
    from etfp.clean.common import CleanError

    _configure_logging()
    try:
        result = build_all()
    except CleanError as exc:
        typer.echo(f"BUILD FAILED: {exc}", err=True)
        raise typer.Exit(1) from None
    for table, rows in sorted(result["clean"].items()):
        typer.echo(f"clean  {table:<16} {rows:>9} rows")
    typer.echo(f"etf_day                 {result['etf_day']:>9} rows")
    if not no_export:
        _export_research()


def _export_research() -> None:
    from etfp.export import export_research
    from etfp.store import Store

    folder = export_research(Store())
    typer.echo(f"research export: {folder}")


@export_app.command("research")
def export_research_cmd() -> None:
    """Write exports/research/<date>/ (parquet, schema, manifest) and LATEST.txt."""
    _configure_logging()
    _export_research()


@app.command()
def alerts(
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Print the alerts that would be sent; send nothing.")
    ] = False,
) -> None:
    """Evaluate alert rules on etf_day and iNAV snapshots; send new ones to Telegram."""
    from etfp.alerts import (
        AlertDeliveryError,
        AlertsAlreadyRunningError,
        MissingCredentialsError,
        run_alerts,
    )
    from etfp.store import Store

    _configure_logging()
    try:
        new = run_alerts(Store(), dry_run=dry_run)
    except (MissingCredentialsError, AlertDeliveryError, AlertsAlreadyRunningError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from None
    typer.echo(f"{len(new)} alert(s) {'would be sent' if dry_run else 'sent'}")


# -- run daily ----------------------------------------------------------------------
#
# One command for the scheduler: collect -> build -> export -> alerts. The order, the lock
# and the run record live in etfp.ops.run_daily; the functions below are the real jobs it
# calls (tests replace them with fakes).

# Snapshots that only exist as "today's latest file". The morning run works on the previous
# session, so fetching them then would store the morning's file under the wrong day.
_EVENING_ONLY_SOURCES = (jobs.ETF_LIST, jobs.NAVALL)


def _collect_step(day: date, phase: ops.Phase) -> ops.StepReport:
    """Download the day's raw files. A failed source is reported; the others still run.

    Evening: every source `jobs.plan_daily` supports, for `day`. Files already stored are
    skipped, so a re-run is cheap and safe.

    Morning (finishing the previous session, `day`):
      1. every source except the evening-only snapshots, WITHOUT force: this only fills
         holes (for example an NSE file that was not yet posted at 19:45), because stored
         files are skipped;
      2. the AMFI history window WITH force: overseas NAVs for `day` are published the next
         morning, so the window saved last evening is incomplete. The raw store keeps both
         versions (the new bytes go beside the old as ".v2"); cleaning uses the newest.
    """
    now = now_ist()
    if phase == "evening":
        passes = [(list(jobs.DAILY_SOURCES), False)]
    else:
        held_back = (*_EVENING_ONLY_SOURCES, jobs.AMFI_HISTORY)
        others = [s for s in jobs.DAILY_SOURCES if s not in held_back]
        passes = [(others, False), ([jobs.AMFI_HISTORY], True)]
    settings = load_collect_settings()
    store = RawStore(paths.raw_dir())
    outcomes: list[jobs.Outcome] = []
    with _build_bulk_client(settings) as client:
        for sources, force in passes:
            plan = jobs.plan_daily(day, sources, now=now)
            for note in plan.notes:
                typer.echo(f"NOTE        {note}")
            outcomes += jobs.run_units(
                plan.units,
                client,
                store,
                force=force,
                on_outcome=lambda o: _print_outcome(o, quiet_skips=False),
            )
    counts = jobs.summarize(outcomes)
    detail = (
        f"{counts['fetched']} fetched, {counts['exists']} already stored, "
        f"{counts['unavailable']} not available, {counts['failed']} FAILED"
    )
    failed = [
        {
            "source": o.unit.source,
            "day": o.unit.day.isoformat(),
            "name": o.unit.name,
            "detail": o.detail,
        }
        for o in outcomes
        if o.status == "failed"
    ]
    if failed:
        names = ", ".join(sorted({f["source"] for f in failed}))
        return ops.StepReport("failed", f"{detail} (failed: {names})", {"failed": failed})
    return ops.StepReport("ok", detail, {"counts": counts})


def _build_step() -> ops.StepReport:
    """Raw -> clean tables -> etf_day. A bad raw file (CleanError) fails the step."""
    from etfp.build import build_all
    from etfp.clean.common import CleanError

    try:
        result = build_all()
    except CleanError as exc:
        return ops.StepReport("failed", f"CleanError: {exc}")
    for table, rows in sorted(result["clean"].items()):
        typer.echo(f"clean  {table:<16} {rows:>9} rows")
    typer.echo(f"etf_day                 {result['etf_day']:>9} rows")
    return ops.StepReport(
        "ok", f"etf_day {result['etf_day']} rows, {len(result['clean'])} clean tables"
    )


def _export_step() -> ops.StepReport:
    from etfp.export import export_research
    from etfp.store import Store

    folder = export_research(Store())
    return ops.StepReport("ok", f"research export: {folder}")


def _alerts_step() -> ops.StepReport:
    """Send new alerts. No Telegram credentials is a warning, not a failure.

    Without credentials nothing can be sent, so we also print what WOULD have been sent
    (a dry run never sends and never touches the sent-file) so the log still shows it.
    """
    from etfp.alerts import (
        AlertDeliveryError,
        AlertsAlreadyRunningError,
        MissingCredentialsError,
        run_alerts,
    )
    from etfp.store import Store

    try:
        new = run_alerts(Store(), dry_run=False)
    except MissingCredentialsError as exc:
        message = f"alerts NOT sent, Telegram credentials missing: {exc}"
        try:
            would = run_alerts(Store(), dry_run=True)
            message += f" ({len(would)} alert(s) would have been sent)"
        except Exception as dry_exc:  # the preview is a courtesy; never let it fail the run
            message += f" (preview also failed: {type(dry_exc).__name__}: {dry_exc})"
        return ops.StepReport("warning", message)
    except (AlertDeliveryError, AlertsAlreadyRunningError) as exc:
        return ops.StepReport("failed", f"{type(exc).__name__}: {exc}")
    return ops.StepReport("ok", f"{len(new)} alert(s) sent")


def _real_steps() -> ops.DailySteps:
    return ops.DailySteps(
        collect=_collect_step, build=_build_step, export=_export_step, alerts=_alerts_step
    )


@run_app.command("daily")
def run_daily_cmd(
    day_text: Annotated[
        str | None,
        typer.Option(
            "--date",
            help="Trading day to process, YYYY-MM-DD. Evening default: today. Morning default: "
            "the last NSE session before today (its overseas NAVs appear this morning).",
        ),
    ] = None,
    phase: Annotated[
        str | None,
        typer.Option(
            "--phase",
            help="evening = collect all sources, build, export, alert. morning = fill gaps, "
            "re-fetch the AMFI NAV window, build, export, alert. Default: evening from 18:00 "
            "India time, otherwise morning.",
        ),
    ] = None,
    no_alerts: Annotated[
        bool, typer.Option("--no-alerts", help="Skip the alerts step (nothing is sent).")
    ] = False,
) -> None:
    """The scheduled run: collect, build, export, alerts. Exit 0 only if every step worked.

    A failed download does not stop the build, and a failed build does not stop the alerts
    (the stale-data alert should fire). Missing Telegram credentials is a warning only.
    Exit codes: 0 ok (or nothing to do: NSE closed), 1 a step failed, 2 bad option,
    3 another run is in progress.
    """
    now = now_ist()
    chosen = phase if phase is not None else ops.default_phase(now)
    if chosen not in ops.PHASES:
        typer.echo(
            f"error: --phase must be one of {', '.join(ops.PHASES)}, got {chosen!r}", err=True
        )
        raise typer.Exit(EXIT_USAGE)
    requested = _parse_day(day_text, "--date") if day_text else None
    try:
        day, closed_message = ops.resolve_run_day(chosen, requested, now)
    except ValueError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(EXIT_USAGE) from None
    if closed_message:
        typer.echo(closed_message)
        return
    _configure_logging()
    typer.echo(f"run daily: {chosen} phase for trading day {day.isoformat()}")
    try:
        result = ops.run_daily(
            day=day,
            phase=chosen,
            steps=_real_steps(),
            state_dir=paths.state_dir(),
            no_alerts=no_alerts,
            clock=now_ist,
            out=typer.echo,
            err=lambda text: typer.echo(text, err=True),
        )
    except ops.RunLockedError as exc:
        typer.echo(f"error: {exc}. Nothing was run.", err=True)
        raise typer.Exit(EXIT_LOCKED) from None
    if result.exit_code:
        raise typer.Exit(result.exit_code)


@ops_app.command("launchd-plist")
def launchd_plist(
    job: Annotated[
        str,
        typer.Option(
            "--job",
            help=f"Which job to print: {', '.join(ops.JOBS)}. Default: inav (the recorder).",
        ),
    ] = "inav",
) -> None:
    """Print a macOS launchd job (installs nothing). Redirect it into a .plist file."""
    if job not in ops.JOBS:
        typer.echo(f"error: --job must be one of {', '.join(ops.JOBS)}, got {job!r}", err=True)
        raise typer.Exit(EXIT_USAGE)
    try:
        uv_path = ops.find_uv()
    except FileNotFoundError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(EXIT_USAGE) from None
    text = ops.render_plist(job, project_root=paths.project_root(), uv_path=uv_path)
    typer.echo(text, nl=False)
    if job == "daily-evening":
        typer.echo(ops.MUHURAT_NOTE, err=True)  # stderr, so `> file.plist` stays clean


if __name__ == "__main__":
    app()
