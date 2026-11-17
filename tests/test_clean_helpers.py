"""Shared helpers for the clean-layer tests (no tests in this file).

``make_raw_root`` copies the saved sample files in tests/fixtures into a
temporary raw store, in the same data/raw/<source>/<date>/ layout the
collectors produce (through RawStore, so the manifest and file permissions are
real too). Dates are the ones the files describe.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

from etfp.rawstore import RawStore
from etfp.store import Store

FIXTURES = Path(__file__).parent / "fixtures"
FETCHED = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)

# (source folder, folder date, raw file name, fixture path under tests/fixtures)
RAW_FILES: list[tuple[str, date, str, str]] = [
    ("nse_bhavcopy_full", date(2021, 1, 4), "sec_bhavdata_full_04012021.csv",
     "nse_bhavcopy/sec_bhavdata_full_04012021.csv"),
    ("nse_bhavcopy_full", date(2026, 11, 9), "sec_bhavdata_full_09112026.csv",
     "nse_bhavcopy/sec_bhavdata_full_09112026.csv"),
    ("nse_udiff_bhavcopy", date(2026, 11, 9), "BhavCopy_NSE_CM_0_0_0_20261109_F_0000.csv.zip",
     "nse_udiff/BhavCopy_NSE_CM_0_0_0_20261109_F_0000.csv.zip"),
    ("nse_security_master", date(2024, 7, 1), "NSE_CM_security_01072024.csv.gz",
     "nse_secmaster/NSE_CM_security_01072024.csv.gz"),
    ("nse_security_master", date(2025, 1, 2), "NSE_CM_security_02012025.csv.gz",
     "nse_secmaster/NSE_CM_security_02012025.csv.gz"),
    ("nse_security_master", date(2026, 9, 4), "NSE_CM_security_04092026.csv.gz",
     "nse_secmaster/NSE_CM_security_04092026.csv.gz"),
    ("nse_security_master", date(2026, 11, 9), "NSE_CM_security_09112026.csv.gz",
     "nse_secmaster/NSE_CM_security_09112026.csv.gz"),
    ("nse_etf_seclist", date(2026, 11, 10), "eq_etfseclist.csv",
     "nse_etf_listfile/eq_etfseclist.csv"),
    ("amfi_navall", date(2026, 11, 10), "NAVAll.txt", "amfi/NAVAll.txt"),
    ("amfi_nav_history", date(2026, 9, 10), "navhistory_20260901_20260910.txt",
     "amfi/navhistory_20260901_20260910.txt"),
    ("amfi_nav_history", date(2021, 1, 8), "navhistory_20210101_20210108.txt",
     "amfi/navhistory_20210101_20210108.txt"),
    ("nse_etf_api", date(2026, 11, 10), "etf_212000.json", "nse_etf_api/api_etf.json"),
    ("amc_inav_motilal", date(2026, 11, 10), "inav_155000.json",
     "amc_inav/motilal_GetINAVandPrice.json"),
    ("amc_inav_mirae", date(2026, 11, 10), "inav_155000.json", "amc_inav/mirae_ticker.json"),
    ("amc_inav_nippon", date(2026, 11, 10), "inav_155000.json",
     "amc_inav/nippon_DetailsFill.json"),
    # BSE daily bhavcopy (R9): one legacy zip, one UDiFF CSV
    ("bse_bhavcopy", date(2021, 1, 4), "EQ_ISINCODE_040121.zip",
     "bse_bhavcopy/EQ_ISINCODE_040121.zip"),
    ("bse_bhavcopy", date(2026, 11, 12), "BhavCopy_BSE_CM_0_0_0_20261112_F_0000.CSV",
     "bse_bhavcopy/BhavCopy_BSE_CM_0_0_0_20261112_F_0000.CSV"),
    # NSE securities lending (R8)
    ("nse_slb_eligible", date(2021, 1, 4), "SLB_ELG_SEC_04012021.csv",
     "nse_slb/SLB_ELG_SEC_04012021.csv"),
    ("nse_slb_eligible", date(2026, 11, 12), "SLB_ELG_SEC_12112026.csv",
     "nse_slb/SLB_ELG_SEC_12112026.csv"),
    ("nse_slb_bhavcopy", date(2021, 1, 4), "SLBM_BC_04012021.DAT",
     "nse_slb/SLBM_BC_04012021.DAT"),
    ("nse_slb_bhavcopy", date(2026, 11, 12), "SLBM_BC_12112026.DAT",
     "nse_slb/SLBM_BC_12112026.DAT"),
    # FBIL rupee reference rates (D43): a real answer for 2-13 Nov 2026 (data to 9 Nov)
    ("fbil_refrates", date(2026, 11, 18), "fbil_refrates_2026-11-02_2026-11-13.json",
     "fbil_refrates/fbil_refrates_2026-11-02_2026-11-13.json"),
    # IBJA domestic gold/silver rates (bullion reference price)
    ("ibja_rates", date(2026, 11, 14), "ibjarates_home.html",
     "ibja_rates/ibjarates_home_20261114.html"),
    # notice reader: SEBI Legal listing and NSE circulars (keyword-tagged notices)
    ("sebi_listing", date(2026, 9, 5), "sebi_legal_20260825_20260905_p1.html",
     "notices/sebi_legal_20260825_20260905_p1.html"),
    ("nse_circulars", date(2026, 9, 1), "nse_circulars_20260901_20260901.json",
     "notices/nse_circulars_20260901_20260901.json"),
]  # fmt: skip


def save_raw(root: Path, source: str, day: date, name: str, content: bytes) -> None:
    """Store bytes in <root>/data/raw exactly as a collector would."""
    RawStore(root / "data" / "raw").save(
        source=source, day=day, name=name, content=content,
        url="https://example.invalid/fixture", status_code=200, fetched_at=FETCHED,
    )  # fmt: skip


def fixture_bytes(rel: str) -> bytes:
    return (FIXTURES / rel).read_bytes()


def make_raw_root(root: Path, *, skip_sources: tuple[str, ...] = ()) -> Path:
    """Fill <root>/data/raw with the sample files. Returns ``root``."""
    for source, day, name, rel in RAW_FILES:
        if source not in skip_sources:
            save_raw(root, source, day, name, fixture_bytes(rel))
    return root


def build_sample(factory) -> tuple[Path, Store]:
    """Make a sample raw root, run build_clean on it and return (root, store)."""
    from etfp.clean import build_clean

    root = make_raw_root(factory.mktemp("proj"))
    build_clean(root)
    return root, Store(root / "data" / "clean")
