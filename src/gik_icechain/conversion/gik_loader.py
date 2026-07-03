"""Load and validate GIK Parquet reference files from HuggingFace.

The GIK pipeline (icpac-igad/grib-index-kerchunk) produces one Parquet file
per ECMWF forecast run (~140 KB each), containing byte-range references to
GRIB2 objects on s3://ecmwf-forecasts.

Dataset stats (as of March 2026):
  - 150 246 Parquet files
  - 737 days covered (2024-03-01 to 2026-03-07)
  - 18.5 GB total metadata  (~48 000× compression vs underlying GRIB2)
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import structlog
from huggingface_hub import HfFileSystem
from pydantic import BaseModel, field_validator

log = structlog.get_logger(__name__)

GIK_HF_DATASET = "E4DRR/gik-ecmwf-par"
GIK_CATALOG_FILE = "catalog.parquet"

VALID_RUN_HOURS = (0, 6, 12, 18)

FLOOD_RELEVANT_VARS = ["tp", "2t", "10u", "10v", "ro"]


class GIKManifestEntry(BaseModel):
    """A single GIK reference entry pointing at one GRIB2 message."""

    date: date
    run_hour: int
    step: int  # forecast step in hours (0-360)
    member: int  # 0 = control, 1-50 = perturbed members
    variable: str
    level: int | None
    s3_uri: str
    byte_offset: int
    byte_length: int

    @field_validator("run_hour")
    @classmethod
    def validate_run_hour(cls, v: int) -> int:
        if v not in VALID_RUN_HOURS:
            raise ValueError(f"run_hour must be one of {VALID_RUN_HOURS}, got {v}")
        return v

    @field_validator("member")
    @classmethod
    def validate_member(cls, v: int) -> int:
        if not (0 <= v <= 50):
            raise ValueError(f"member must be 0-50, got {v}")
        return v


class GIKCatalog:
    """Interface to the GIK Parquet catalog on HuggingFace.

    The catalog.parquet file indexes all available Parquet reference files
    by date and run hour.
    """

    def __init__(
        self,
        hf_dataset: str = GIK_HF_DATASET,
        catalog_file: str = GIK_CATALOG_FILE,
    ) -> None:
        self.hf_dataset = hf_dataset
        self.catalog_file = catalog_file
        self._catalog: pd.DataFrame | None = None
        self._fs = HfFileSystem()

    def load_catalog(self, cache_dir: Path | None = None) -> pd.DataFrame:
        """Download and optionally cache the catalog.parquet index to disk.

        The HuggingFace catalog schema:
          date (str "YYYYMMDD"), run (str "HHz"), member (str "control"/"ens_NN"),
          hf_path (str), filename (str), size_bytes (int).

        Args:
            cache_dir: If provided, the catalog is written to
                       ``{cache_dir}/{dataset}_catalog.parquet`` on first fetch
                       and read from disk on subsequent calls.
        """
        if self._catalog is not None:
            return self._catalog

        cache_file: Path | None = None
        if cache_dir is not None:
            slug = self.hf_dataset.replace("/", "_")
            cache_file = Path(cache_dir) / f"{slug}_catalog.parquet"
            if cache_file.exists():
                catalog = pd.read_parquet(cache_file)
                self._catalog = catalog
                log.info("gik_catalog_loaded_from_cache", path=str(cache_file), rows=len(catalog))
                return catalog

        catalog_path = f"datasets/{self.hf_dataset}/{self.catalog_file}"
        log.info("loading_gik_catalog", path=catalog_path)
        with self._fs.open(catalog_path) as f:
            catalog = pd.read_parquet(f)

        # Normalise: parse "YYYYMMDD" date strings once for fast comparisons.
        catalog["_date_parsed"] = pd.to_datetime(catalog["date"], format="%Y%m%d").dt.date
        self._catalog = catalog

        if cache_file is not None:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            catalog.to_parquet(cache_file)
            log.info("gik_catalog_cached", path=str(cache_file))

        log.info(
            "gik_catalog_loaded",
            rows=len(catalog),
            date_range=f"{catalog['date'].min()} to {catalog['date'].max()}",
        )
        return catalog

    def list_available_dates(self) -> list[date]:
        """Return sorted list of all dates covered by the GIK dataset."""
        catalog = self.load_catalog()
        return sorted(catalog["_date_parsed"].unique().tolist())

    def get_coverage_gap(
        self,
        start: date = date(2023, 5, 1),
        end: date = date(2026, 8, 31),
    ) -> list[date]:
        """Identify dates not yet covered by the GIK dataset.

        Missing dates require the gap-fill procedure (scripts/run_gap_fill.py).
        """
        available = set(self.list_available_dates())
        all_dates = {start + timedelta(days=i) for i in range((end - start).days + 1)}
        missing = sorted(all_dates - available)
        log.info(
            "coverage_gap_identified",
            missing_days=len(missing),
            first_missing=missing[0] if missing else None,
            last_missing=missing[-1] if missing else None,
        )
        return missing

    def get_parquet_paths(
        self,
        start: date,
        end: date,
        run_hours: tuple[int, ...] = (0,),
        variables: list[str] | None = None,
    ) -> list[str]:
        """Return HuggingFace hf:// paths to Parquet files for a date/hour range.

        The catalog indexes one file per (date, run_hour, member) - no per-variable
        filtering is possible here.  Variable filtering is applied downstream when
        each Parquet file is loaded by VirtualiZarr.

        Args:
            start:     First date (inclusive).
            end:       Last date (inclusive).
            run_hours: Tuple of run hours to include, e.g. (0,) or (0, 12).
            variables: Ignored at the catalog level (logged as info).
        """
        catalog = self.load_catalog()

        # run column is "00z", "06z", "12z", "18z"
        run_strs = {f"{h:02d}z" for h in run_hours}

        mask = (
            (catalog["_date_parsed"] >= start)
            & (catalog["_date_parsed"] <= end)
            & (catalog["run"].isin(run_strs))
        )

        if variables:
            log.info(
                "catalog_variable_filter_note",
                msg="Catalog indexes whole files; per-file filter applied by VirtualiZarr.",
                variables=variables,
            )

        filtered = catalog[mask]

        if len(filtered) == 0:
            log.warning("no_parquet_files_found", start=start, end=end, run_hours=run_hours)
            return []

        paths = (
            "hf://datasets/" + self.hf_dataset + "/" + filtered["hf_path"]
        ).tolist()
        log.info("parquet_paths_resolved", count=len(paths), date_range=f"{start} to {end}")
        return paths


def load_gik_parquet(
    path: str,
    variables: list[str] | None = None,
) -> pd.DataFrame:
    """Load a single GIK Parquet file as a byte-range reference DataFrame.

    Each row represents one GRIB2 message with columns:
      uri, byte_offset, byte_length, variable, level, step, member, date, run_hour.

    Args:
        path:      Path or HuggingFace URL to the Parquet file.
        variables: Optional list of GRIB2 shortNames to filter.
    """
    log.debug("loading_parquet", path=path)
    df = pd.read_parquet(path)

    if variables:
        df = df[df["shortName"].isin(variables)]

    rename_map = {
        "shortName": "variable",
        "levelType": "level_type",
        "level": "level",
        "stepRange": "step",
        "number": "member",
        "uri": "uri",
        "offset": "byte_offset",
        "length": "byte_length",
    }
    df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})
    log.debug("parquet_loaded", rows=len(df), variables=df["variable"].unique().tolist())
    return df
