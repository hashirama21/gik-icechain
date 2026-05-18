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

FLOOD_RELEVANT_VARS = ["tp", "2t", "10u", "10v", "sro", "ssro"]


class GIKManifestEntry(BaseModel):
    """A single GIK reference entry pointing at one GRIB2 message."""

    date:        date
    run_hour:    int
    step:        int         # forecast step in hours (0–360)
    member:      int         # 0 = control, 1–50 = perturbed members
    variable:    str
    level:       int | None  # pressure level in hPa; None for surface fields
    s3_uri:      str
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
            raise ValueError(f"member must be 0–50, got {v}")
        return v


class GIKCatalog:
    """Interface to the GIK Parquet catalog on HuggingFace.

    The catalog.parquet file indexes all available Parquet reference files
    by date and run hour.
    """

    def __init__(self, hf_dataset: str = GIK_HF_DATASET) -> None:
        self.hf_dataset = hf_dataset
        self._catalog: pd.DataFrame | None = None
        self._fs = HfFileSystem()

    def load_catalog(self, cache_dir: Path | None = None) -> pd.DataFrame:
        """Download and cache the catalog.parquet index."""
        if self._catalog is not None:
            return self._catalog

        catalog_path = f"datasets/{self.hf_dataset}/{GIK_CATALOG_FILE}"
        log.info("loading_gik_catalog", path=catalog_path)

        with self._fs.open(catalog_path) as f:
            self._catalog = pd.read_parquet(f)

        log.info(
            "gik_catalog_loaded",
            rows=len(self._catalog),
            date_range=f"{self._catalog['date'].min()} to {self._catalog['date'].max()}",
        )
        return self._catalog

    def list_available_dates(self) -> list[date]:
        """Return sorted list of all dates covered by the GIK dataset."""
        catalog = self.load_catalog()
        return sorted(pd.to_datetime(catalog["date"]).dt.date.unique().tolist())

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
        """Return HuggingFace paths to Parquet files for a date range.

        Returns paths of the form:
          hf://datasets/E4DRR/gik-ecmwf-par/run_par_ecmwf/YYYY/MM/YYYYMMDD/HH/...
        """
        catalog = self.load_catalog()

        mask = (
            (pd.to_datetime(catalog["date"]).dt.date >= start)
            & (pd.to_datetime(catalog["date"]).dt.date <= end)
            & (catalog["run_hour"].isin(run_hours))
        )

        if variables:
            mask &= catalog["variable"].isin(variables)

        filtered = catalog[mask]

        if len(filtered) == 0:
            log.warning("no_parquet_files_found", start=start, end=end, run_hours=run_hours)
            return []

        paths = [
            f"hf://datasets/{self.hf_dataset}/{row['path']}"
            for _, row in filtered.iterrows()
        ]
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
        "level":     "level",
        "stepRange": "step",
        "number":    "member",
        "uri":       "uri",
        "offset":    "byte_offset",
        "length":    "byte_length",
    }
    df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})
    log.debug("parquet_loaded", rows=len(df), variables=df["variable"].unique().tolist())
    return df
