"""The clean-data store: DuckDB tables plus one Parquet file per table.

Why two formats: Parquet is the portable copy (the research side and the
open dataset read it directly); DuckDB is the fast SQL engine the dashboard
and the feature code query. They are always written together from the same
bytes, so they cannot disagree.

Where things live (all under ``data/clean/``, see ``etfp.paths.clean_dir``):

* ``etf.duckdb``      the database
* ``<table>.parquet`` one file per table

Every table has a *schema* in the registry below: column names, types, which
columns together form the primary key (the "no two rows may share these
values" rule), and a one-line plain-words definition. ``write_table`` forces
the data into that schema and refuses to write anything that breaks it:

* a missing, extra or mistyped column is an error,
* a missing value in a primary-key column or in ``source_file`` is an error
  (project rule: every number must be traceable to a raw file),
* two rows with the same primary key are an error, and the message lists them.

Dates are stored as calendar dates (Python ``date``), never as midnight
timestamps. Timestamps are timezone-aware and always shown in Asia/Kolkata
(IST); the instant is the same whatever zone it is displayed in.

Tables are replaced as a whole (``write_table``). The clean layer is rebuilt
from the raw files, never edited by hand.
"""

from __future__ import annotations

import logging
import os
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from etfp import paths
from etfp.timeutil import IST

logger = logging.getLogger("etfp.store")

DB_NAME = "etf.duckdb"
Dtype = Literal["string", "float", "int", "date", "timestamp", "bool"]

# How each logical type is held in memory (pandas) and on disk (Parquet).
_PANDAS_DTYPE = {"string": "string", "float": "float64", "int": "Int64", "bool": "boolean"}
_ARROW_TYPE = {
    "string": pa.string(),
    "float": pa.float64(),
    "int": pa.int64(),
    "date": pa.date32(),
    "timestamp": pa.timestamp("us", tz="Asia/Kolkata"),
    "bool": pa.bool_(),
}


class StoreError(Exception):
    """Base class for problems with the clean store."""


class SchemaError(StoreError, ValueError):
    """The data does not match the table's registered columns or types."""


class DuplicateKeyError(StoreError, ValueError):
    """Two or more rows share the same primary key."""


class MissingValueError(StoreError, ValueError):
    """A column that must always be filled (key columns, source_file) has gaps."""


@dataclass(frozen=True)
class Column:
    """One column: its name, logical type, plain-words meaning, and whether it may be empty."""

    name: str
    dtype: Dtype
    doc: str
    nullable: bool = True


@dataclass(frozen=True)
class TableSchema:
    """Shape of one clean table.

    ``open_ended`` tables accept extra columns beyond the registered ones and
    store them unchanged. Only the placeholder ``etf_day`` uses this, until the
    features code registers the full column list with ``register_schema``.
    """

    name: str
    primary_key: tuple[str, ...]
    columns: tuple[Column, ...]
    doc: str
    open_ended: bool = False

    def __post_init__(self) -> None:
        names = [c.name for c in self.columns]
        if len(set(names)) != len(names):
            raise ValueError(f"{self.name}: duplicate column names in schema")
        absent = [k for k in self.primary_key if k not in names]
        if absent:
            raise ValueError(f"{self.name}: primary key column(s) {absent} not in the schema")

    @property
    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]

    @property
    def dtypes(self) -> dict[str, Dtype]:
        return {c.name: c.dtype for c in self.columns}

    def required_columns(self) -> list[str]:
        """Columns that may never be empty: the key, ``source_file`` and anything marked so."""
        required = {c.name for c in self.columns if not c.nullable}
        required.update(self.primary_key)
        if "source_file" in self.column_names:
            required.add("source_file")
        return [n for n in self.column_names if n in required]


def _t(name: str, pk: Sequence[str], doc: str, cols: Iterable[Column], **kw: Any) -> TableSchema:
    return TableSchema(name=name, primary_key=tuple(pk), columns=tuple(cols), doc=doc, **kw)


_SRC = (
    "Raw-store path of the file this row came from (lineage), e.g. nse_bhavcopy_full/2026-11-09/..."
)

# ==========================================================================
# The registry (SPEC section 8)
# ==========================================================================
_SCHEMAS: tuple[TableSchema, ...] = (
    _t(
        "etf_master",
        ["symbol"],
        "One row per exchange-traded fund: every ETF on NSE's list plus any in config/etfs.yaml.",
        [
            Column("symbol", "string", "NSE trading symbol, e.g. MONQ50"),
            Column("name", "string", "Fund name (config name if configured, else NSE's)"),
            Column("isin", "string", "12-character international security id"),
            Column("amc", "string", "Fund house (config only)"),
            Column("amfi_scheme_code", "int", "AMFI scheme code used to match NAVs (config only)"),
            Column("underlying_index", "string", "Index the ETF tracks (config only)"),
            Column("underlying_market", "string", "Market of the holdings: US, HK or IN (config)"),
            Column("currency", "string", "Currency of the holdings: USD, HKD or INR (config)"),
            Column("asset_class", "string", "equity, gold or silver (config only)"),
            Column("group_label", "string", "Hypothesis label from config (blocked, unclear, ...)"),
            Column("tracked", "bool", "True when config marks it for fair value and iNAV capture"),
            Column("futures_proxy", "string", "Index future used for fair value: NQ or ES"),
            Column("creation_unit_size", "int", "Units in one creation/redemption basket (config)"),
            Column("listing_date", "date", "Date NSE listed the ETF (NSE list)"),
            Column("face_value", "float", "Face value per unit in rupees (NSE list)"),
            Column("market_lot", "int", "Smallest tradable lot in units (NSE list)"),
            Column("nse_underlying_asset", "string", "NSE's description of the underlying"),
            Column("nse_underlying_type", "string", "NSE's category: EQUITY, DEBT, ... (NSE list)"),
            Column("list_file_date", "date", "Folder date of the NSE list file used"),
            Column("config_file", "string", "Config path when the ETF is in config/etfs.yaml"),
            Column("source_file", "string", "NSE list raw path, or config path if not on the list"),
        ],
    ),
    _t(
        "prices_daily",
        ["symbol", "date"],
        "NSE end-of-day prices and volumes per ETF per trading day (unadjusted for splits).",
        [
            Column("symbol", "string", "NSE trading symbol"),
            Column("date", "date", "Trading date (IST)"),
            Column("series", "string", "NSE series, normally EQ"),
            Column("isin", "string", "ISIN from the UDiFF file (empty before UDiFF exists)"),
            Column("open", "float", "Opening price, rupees"),
            Column("high", "float", "Day high, rupees"),
            Column("low", "float", "Day low, rupees"),
            Column("close", "float", "Official close = average of the last 30 minutes, rupees"),
            Column("last", "float", "Price of the very last trade, rupees"),
            Column("avg_price", "float", "Volume-weighted average price of the whole day"),
            Column("prev_close", "float", "Previous official close, rupees"),
            Column("settle_price", "float", "Settlement price (UDiFF only)"),
            Column("volume", "int", "Units traded"),
            Column("value", "float", "Traded value in rupees (exact from UDiFF, else lacs x 1e5)"),
            Column(
                "value_source",
                "string",
                "udiff (exact), bhavcopy (rounded to Rs 1,000) or bhavcopy_legacy (old file)",
            ),
            Column("trades", "int", "Number of trades"),
            Column("deliv_qty", "int", "Units taken as delivery (bhavcopy only)"),
            Column("deliv_pct", "float", "Delivered units as percent of units traded, 0-100"),
            Column("source_file", "string", _SRC),
            Column("source_file_udiff", "string", "UDiFF raw path merged into this row, if any"),
        ],
    ),
    _t(
        "nav_daily",
        ["symbol", "nav_date"],
        "AMFI net asset value per unit per date for configured ETFs (unadjusted for splits).",
        [
            Column("symbol", "string", "NSE trading symbol (from the AMFI scheme code in config)"),
            Column("scheme_code", "int", "AMFI scheme code"),
            Column("nav_date", "date", "Date the NAV is for (not when it was published)"),
            Column("nav", "float", "NAV per unit, rupees", nullable=False),
            Column(
                "nav_source", "string", "history (AMFI history download) or navall (daily file)"
            ),
            Column("source_file", "string", _SRC),
        ],
    ),
    _t(
        "inav_snapshots",
        ["symbol", "ts", "source"],
        "Intraday snapshots (iNAV, price, order book): one row per source per tick.",
        [
            Column("symbol", "string", "NSE trading symbol"),
            Column("ts", "timestamp", "When WE fetched it (IST). Not the fund house's own stamp"),
            Column(
                "source", "string", "nse_etf_api, nse_quote, amc_motilal, amc_mirae, amc_nippon"
            ),
            Column("last_price", "float", "Last traded exchange price, rupees (NSE sources)"),
            Column("inav", "float", "Fund house's indicative NAV, rupees (AMC sources, NSE quote)"),
            Column("prev_nav", "float", "Previously published end-of-day NAV, rupees"),
            Column("bid", "float", "Best bid price (NSE quote)"),
            Column("ask", "float", "Best ask price (NSE quote)"),
            Column("bid_qty", "int", "Quantity at the best bid"),
            Column("ask_qty", "int", "Quantity at the best ask"),
            Column("total_buy_qty", "int", "Total quantity of all buy orders in the book"),
            Column("total_sell_qty", "int", "Total quantity of all sell orders in the book"),
            Column(
                "depth_json", "string", 'Whole order book: {"bid": [[price, qty], ...], "ask": ...}'
            ),
            Column("volume", "int", "Units traded so far today (NSE sources)"),
            Column("value", "float", "Rupees traded so far today (NSE sources)"),
            Column(
                "data_ts", "timestamp", "The source's own data timestamp (IST), when it gives one"
            ),
            Column("amc_ts_raw", "string", "Fund house's own stamp as text; evidence only (D15)"),
            Column("market_status", "string", "NSE market status text, e.g. Open or Close"),
            Column("source_file", "string", _SRC),
            Column(
                "inav_stale",
                "bool",
                "True when the iNAV repeated exactly for 4+ snapshots in a row in the session "
                "(feed not moving, D49); missing when the row has no iNAV",
            ),
        ],
    ),
    _t(
        "units_daily",
        ["symbol", "date"],
        "Units outstanding per ETF from the NSE security master (IssdCptl), request R1.",
        [
            Column("symbol", "string", "NSE trading symbol"),
            Column("date", "date", "Date in the security-master file name"),
            Column("series", "string", "NSE series the row was read from (EQ preferred)"),
            Column("isin", "string", "ISIN"),
            Column("units_outstanding", "int", "Units issued (IssdCptl)"),
            Column("source_file", "string", _SRC),
        ],
    ),
    _t(
        "band_official",
        ["symbol", "file_date"],
        "NSE's published initial price band (PricRg) and the session it applies to, request R2.",
        [
            Column("symbol", "string", "NSE trading symbol"),
            Column("file_date", "date", "Date in the security-master file name"),
            Column("session_date", "date", "NSE session the band applies to = next trading day"),
            Column("series", "string", "NSE series the row was read from (EQ preferred)"),
            Column("band_low_official", "float", "Lowest allowed price, rupees"),
            Column("band_high_official", "float", "Highest allowed price, rupees"),
            Column("price_range_raw", "string", "PricRg text exactly as published"),
            Column("source_file", "string", _SRC),
        ],
    ),
    _t(
        "underlying_daily",
        ["instrument", "date"],
        "Closing levels of the underlying indices/futures (filled by the Bloomberg importer).",
        [
            Column("instrument", "string", "Instrument name, e.g. NDX or NQ1"),
            Column("date", "date", "Trading date in the instrument's own market"),
            Column("close", "float", "Official close"),
            Column("value_at_1530_ist", "float", "Level at 15:30 IST"),
            Column("source_file", "string", _SRC),
        ],
    ),
    _t(
        "fx_daily",
        ["pair", "date"],
        "Exchange rates (USD/INR, HKD/INR) filled by the Bloomberg or FBIL importer.",
        [
            Column("pair", "string", "Currency pair, e.g. USDINR"),
            Column("date", "date", "Date"),
            Column("rate_1530_ist", "float", "Rate at 15:30 IST"),
            Column("rate_nav", "float", "Rate at the time the fund house strikes NAV"),
            Column("source", "string", "Where the rate came from, e.g. bloomberg or fbil"),
            Column("source_file", "string", _SRC),
        ],
    ),
    _t(
        "units_monthly",
        ["symbol", "month_end"],
        "Month-end AUM and units (AUM / NAV) from disclosures, for months before the daily file.",
        [
            Column("symbol", "string", "NSE trading symbol"),
            Column("month_end", "date", "Last calendar day of the month"),
            Column("aum", "float", "Assets under management in rupees"),
            Column("nav", "float", "NAV per unit used, rupees"),
            Column("units", "float", "AUM / NAV (an estimate, so not a whole number)"),
            Column("source", "string", "Where the AUM came from, e.g. amfi or factsheet"),
            Column("source_file", "string", _SRC),
            Column(
                "nav_date",
                "date",
                "Date of the AMFI NAV used (automatic AMC rows); empty if not given (D51)",
            ),
        ],
    ),
    _t(
        "events",
        ["event_id"],
        "Dated rule changes, limits, suspensions, warnings and notices (requests R5, R16).",
        [
            Column("event_id", "string", "Stable id chosen by the loader"),
            Column("date", "date", "Date of the event (IST)"),
            Column("ts_ist", "timestamp", "Publication time (IST), empty if unknown"),
            Column("type", "string", "rule, limit, suspension, warning or other"),
            Column("category", "string", "rule, cap, caution, amc_notice, fund_status or media"),
            Column("direction", "string", "premium_reducing, premium_preserving or neutral"),
            Column("symbols", "string", "Semicolon-separated affected symbols; empty = all"),
            Column("first_tradable_session", "date", "First NSE session that could react"),
            Column("description", "string", "What happened"),
            Column("url", "string", "Link to the source"),
            Column("source_primary", "string", "URL or raw path of the original document"),
            Column("verified", "bool", "True once the primary document is saved and checked"),
            Column("source_file", "string", "File the row was loaded from"),
        ],
    ),
    _t(
        "fof_monthly",
        ["amfi_scheme_code", "month_end"],
        "Feeder funds (fund of funds) that buy these ETFs, per month (requests R4, R14).",
        [
            Column("amfi_scheme_code", "int", "AMFI scheme code of the feeder fund"),
            Column("month_end", "date", "Last calendar day of the month"),
            Column("fund_name", "string", "Feeder fund name"),
            Column("etf_symbol", "string", "NSE symbol of the ETF it holds"),
            Column("aum", "float", "Feeder AUM in rupees"),
            Column("etf_units_held", "float", "ETF units held by the feeder (monthly portfolio)"),
            Column("fof_net_flow", "float", "Feeder net inflow in rupees (AMFI monthly)"),
            Column("subscription_status", "string", "open, lump_sum_shut, sip_shut or shut"),
            Column("notice_date", "date", "Date of the notice that set the status"),
            Column("source_file", "string", "File the row was loaded from"),
        ],
    ),
    _t(
        "etf_day",
        ["symbol", "date"],
        "PLACEHOLDER for the analysis table. The features code registers the full columns.",
        [
            Column("symbol", "string", "NSE trading symbol"),
            Column("date", "date", "Trading date (IST)"),
        ],
        open_ended=True,
    ),
)

SCHEMAS: dict[str, TableSchema] = {s.name: s for s in _SCHEMAS}


def get_schema(name: str) -> TableSchema:
    try:
        return SCHEMAS[name]
    except KeyError:
        raise KeyError(f"unknown table {name!r}; known tables: {', '.join(SCHEMAS)}") from None


def register_schema(schema: TableSchema, *, replace: bool = False) -> None:
    """Add a table (or, with ``replace=True``, redefine one), e.g. the full ``etf_day``."""
    if schema.name in SCHEMAS and not replace:
        raise ValueError(f"table {schema.name!r} already has a schema; pass replace=True")
    SCHEMAS[schema.name] = schema


# ==========================================================================
# Coercion: force a DataFrame into a schema
# ==========================================================================
def _is_missing(value: Any) -> bool:
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _as_date(value: Any, where: str) -> date | None:
    """A calendar date from a date, a midnight pandas Timestamp or an ISO string."""
    if _is_missing(value):
        return None
    if isinstance(value, pd.Timestamp):
        if value.tzinfo is not None or value != value.normalize():
            raise SchemaError(f"{where}: {value!r} has a time of day or zone; expected a date")
        return value.date()
    if isinstance(value, datetime):
        raise SchemaError(f"{where}: {value!r} is a datetime; expected a date without a time")
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            raise SchemaError(f"{where}: {value!r} is not an ISO date YYYY-MM-DD") from None
    raise SchemaError(f"{where}: {value!r} ({type(value).__name__}) is not a date")


def _coerce_column(series: pd.Series, col: Column, table: str) -> pd.Series:
    where = f"{table}.{col.name}"
    try:
        if col.dtype == "date":
            return pd.Series([_as_date(v, where) for v in series], dtype=object, index=series.index)
        if col.dtype == "timestamp":
            stamps = pd.to_datetime(series)
            if stamps.dt.tz is None and stamps.notna().any():
                raise SchemaError(f"{where}: timestamps must be timezone-aware, got naive values")
            if stamps.dt.tz is None:  # all missing
                stamps = stamps.dt.tz_localize(IST)
            return stamps.dt.tz_convert(IST).dt.as_unit("us")
        if col.dtype == "float":
            return pd.to_numeric(series, errors="raise").astype("float64")
        if col.dtype == "int":
            numbers = pd.to_numeric(series, errors="raise")
            filled = numbers.dropna()
            if len(filled) and (filled != filled.round()).any():
                raise SchemaError(f"{where}: has non-whole numbers; expected a count")
            return (
                numbers.round().astype("Int64")
                if numbers.dtype.kind == "f"
                else numbers.astype("Int64")
            )
        return series.astype(_PANDAS_DTYPE[col.dtype])
    except (TypeError, ValueError) as exc:
        if isinstance(exc, SchemaError):
            raise
        raise SchemaError(f"{where}: cannot convert to {col.dtype}: {exc}") from exc


def coerce(df: pd.DataFrame, schema: TableSchema) -> pd.DataFrame:
    """Return ``df`` with exactly the schema's columns, in order, with the schema's types."""
    have = list(df.columns)
    missing = [c for c in schema.column_names if c not in have]
    extra = [c for c in have if c not in schema.column_names]
    if missing or (extra and not schema.open_ended):
        raise SchemaError(
            f"{schema.name}: columns do not match the schema; missing {missing}, unexpected {extra}"
        )
    # Build every column first and make the frame in ONE step. Adding extra columns one by
    # one (``out[name] = ...``) splits pandas' memory into many small blocks; with a wide
    # open-ended table that is slow and pandas warns "DataFrame is highly fragmented".
    columns = {c.name: _coerce_column(df[c.name], c, schema.name) for c in schema.columns}
    for name in extra:  # open-ended tables only, kept as they are
        columns[name] = df[name]
    out = pd.DataFrame(columns, index=df.index)
    return out.reset_index(drop=True)


def check_required(df: pd.DataFrame, schema: TableSchema) -> None:
    """Raise if a must-be-filled column (key, source_file, ...) has empty values."""
    for name in schema.required_columns():
        bad = df[name].isna()
        if bad.any():
            rows = [int(i) for i in df.index[bad][:5]]
            raise MissingValueError(
                f"{schema.name}: column {name!r} is empty in {int(bad.sum())} row(s) "
                f"(first row numbers: {rows}); it must always be filled"
            )


def check_unique_key(df: pd.DataFrame, schema: TableSchema, *, limit: int = 5) -> None:
    """Raise ``DuplicateKeyError`` naming the duplicated keys (and their files, if known)."""
    key = list(schema.primary_key)
    dupes = df.duplicated(subset=key, keep=False)
    if not dupes.any():
        return
    groups = df[dupes].groupby(key, sort=True, dropna=False)
    lines = []
    for values, group in list(groups)[:limit]:
        values = values if isinstance(values, tuple) else (values,)
        label = ", ".join(f"{k}={v}" for k, v in zip(key, values, strict=True))
        files = ""
        if "source_file" in group.columns:
            files = " in " + " | ".join(sorted({str(f) for f in group["source_file"]}))
        lines.append(f"  {label}: {len(group)} rows{files}")
    n_keys = groups.ngroups
    more = f" (showing {limit} of {n_keys})" if n_keys > limit else ""
    raise DuplicateKeyError(
        f"{schema.name}: {n_keys} primary key(s) {tuple(key)} appear more than once{more}:\n"
        + "\n".join(lines)
    )


def _to_arrow(df: pd.DataFrame, schema: TableSchema) -> pa.Table:
    """Arrow table with the registered columns cast to their exact on-disk types."""
    table = pa.Table.from_pandas(df, preserve_index=False)
    table = table.replace_schema_metadata(None)
    for i, name in enumerate(table.column_names):
        if name in schema.dtypes:
            field = pa.field(name, _ARROW_TYPE[schema.dtypes[name]])
            table = table.set_column(i, field, table.column(i).cast(field.type))
    return table


# ==========================================================================
# The store
# ==========================================================================
class Store:
    """Reads and writes the clean tables.

    ``Store()`` uses ``data/clean`` under the project root; tests pass a
    temporary folder: ``Store(tmp_path / "clean")``.
    """

    def __init__(self, clean_dir: Path | None = None) -> None:
        self.dir = Path(clean_dir) if clean_dir is not None else paths.clean_dir()

    # -- locations -----------------------------------------------------------
    @property
    def db_path(self) -> Path:
        return self.dir / DB_NAME

    def parquet_path(self, table: str) -> Path:
        get_schema(table)  # also validates the name, so it can never hold a path
        return self.dir / f"{table}.parquet"

    def has_table(self, table: str) -> bool:
        return self.parquet_path(table).exists()

    def tables(self) -> list[str]:
        return [name for name in SCHEMAS if self.has_table(name)]

    # -- writing -------------------------------------------------------------
    def write_table(self, name: str, df: pd.DataFrame) -> int:
        """Replace a table with ``df``. Returns the row count. Nothing is written on error."""
        schema = get_schema(name)
        data = coerce(df, schema)
        check_required(data, schema)
        check_unique_key(data, schema)
        data = data.sort_values(list(schema.primary_key), kind="stable").reset_index(drop=True)

        self.dir.mkdir(parents=True, exist_ok=True)
        target = self.parquet_path(name)
        fd, tmp_name = tempfile.mkstemp(dir=self.dir, prefix=f".{name}.", suffix=".parquet.tmp")
        os.close(fd)
        tmp = Path(tmp_name)
        try:
            pq.write_table(_to_arrow(data, schema), tmp, compression="zstd")
            os.replace(tmp, target)  # readers never see a half-written file
        finally:
            tmp.unlink(missing_ok=True)

        # DuckDB table is rebuilt from the parquet file just written: one source of truth.
        with self._connect() as con:
            con.execute("BEGIN")
            try:
                con.execute(
                    f'CREATE OR REPLACE TABLE "{name}" AS SELECT * FROM read_parquet(?)',
                    [str(target)],
                )
                con.execute("COMMIT")
            except BaseException:
                con.execute("ROLLBACK")
                raise
        logger.info("wrote %s: %d rows", name, len(data))
        return len(data)

    def ensure_table(self, name: str) -> bool:
        """Create the table empty if it does not exist yet. Never touches an existing one.

        Returns True if it created one. Lets the dashboard query tables that no
        importer has filled yet (Bloomberg, events, feeder funds).
        """
        if self.has_table(name):
            return False
        schema = get_schema(name)
        self.write_table(name, pd.DataFrame({n: [] for n in schema.column_names}))
        return True

    # -- reading -------------------------------------------------------------
    def read_table(self, name: str, columns: Sequence[str] | None = None) -> pd.DataFrame:
        """Read a whole table with the registered types. Raises if it was never written."""
        schema = get_schema(name)
        path = self.parquet_path(name)
        if not path.exists():
            raise FileNotFoundError(f"clean table {name!r} has not been built yet: {path}")
        # Int64 keeps whole numbers exact (plain pandas would turn gaps into floats).
        df = pq.read_table(path).to_pandas(types_mapper={pa.int64(): pd.Int64Dtype()}.get)
        df = coerce(df, schema)
        if columns is not None:
            unknown = [c for c in columns if c not in df.columns]
            if unknown:
                raise KeyError(f"{name}: unknown column(s) {unknown}")
            df = df[list(columns)]
        return df

    def _connect(self, *, read_only: bool = False) -> duckdb.DuckDBPyConnection:
        self.dir.mkdir(parents=True, exist_ok=True)
        con = duckdb.connect(str(self.db_path), read_only=read_only)
        con.execute("SET TimeZone = 'Asia/Kolkata'")  # show timestamps in IST
        return con

    def query(self, sql: str, params: Sequence[Any] | None = None) -> pd.DataFrame:
        """Run a read-only SQL query against the database and return a DataFrame.

        Note: use this instead of ``fetchall`` on a raw connection; reading
        timestamps through ``fetchall`` needs the optional ``pytz`` package.
        """
        if not self.db_path.exists():
            raise FileNotFoundError(f"no clean database yet: {self.db_path}")
        with self._connect(read_only=True) as con:
            return con.execute(sql, list(params or [])).fetchdf()
