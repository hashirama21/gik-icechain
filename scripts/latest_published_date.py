"""Print the latest forecast date available in the published era-groups store.

Reads only each era group's ``time`` array (standard codecs, no gribberish),
clamps to yesterday, and prints the date as the last stdout line.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import numpy as np

from gik_icechain.conversion.icechunk_writer import IceChainStore
from gik_icechain.shared.config import load_config


def latest_published_date(config_path: Path) -> date:
    import zarr
    from xarray.coding.times import decode_cf_datetime

    cfg = load_config(config_path)
    src = cfg.component2.source_store
    if src.layout != "era_groups" or not src.uri:
        raise SystemExit(f"{config_path} does not define an era_groups source store with a uri")

    store = IceChainStore(
        src.uri,
        region=src.region,
        endpoint_url=src.endpoint_url,
        anonymous=src.anonymous,
        manifest_preload=src.manifest_preload,
    )
    store.open()
    zg = zarr.open_group(store.readonly_session().store, mode="r")

    latest: date | None = None
    for era in src.era_groups:
        try:
            arr = zg[f"{era.group}/time"]
        except KeyError:
            continue
        raw = np.asarray(arr[:])
        units = arr.attrs.get("units")
        if isinstance(units, str):
            calendar = arr.attrs.get("calendar", "standard")
            raw = decode_cf_datetime(raw, units, calendar if isinstance(calendar, str) else None)
        era_max = np.asarray(raw).astype("datetime64[D]").max().astype(date)
        if era.end is not None:
            era_max = min(era_max, era.end)
        if era_max >= era.start and (latest is None or era_max > latest):
            latest = era_max

    yesterday = date.today() - timedelta(days=1)
    return min(latest, yesterday) if latest else yesterday


if __name__ == "__main__":
    repo_root = Path(__file__).resolve().parents[1]
    print(latest_published_date(repo_root / "configs" / "published_store.yaml"))
