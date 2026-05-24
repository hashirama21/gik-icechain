#!/usr/bin/env python3
"""Download reference data required by GIK-IceChain.

Downloads:
  admin    — East Africa admin-1 boundaries (GADM v4.1, GeoPackage)
  thresholds — Pre-computed CMORPH GEV thresholds from HuggingFace
  enso_iod  — ENSO/IOD index CSV (ONI + DMI, 1980–present)

Usage:
    python scripts/download_data.py --component all
    python scripts/download_data.py --component admin
    python scripts/download_data.py --component thresholds --output data/cmorph_thresholds/
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))


def download_admin_boundaries(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    dest = output_dir / "east_africa_admin1.gpkg"
    if dest.exists():
        print(f"  Already exists: {dest}")
        return

    print("  Downloading admin-1 boundaries from HuggingFace …")
    from huggingface_hub import hf_hub_download

    path = hf_hub_download(
        repo_id="E4DRR/gik-ecmwf-par",
        filename="admin_boundaries/east_africa_admin1.gpkg",
        repo_type="dataset",
        local_dir=str(output_dir),
    )
    print(f"  Saved: {path}")


def download_cmorph_thresholds(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    print("  Downloading CMORPH GEV thresholds from HuggingFace …")
    from huggingface_hub import snapshot_download

    snapshot_download(
        repo_id="E4DRR/gik-ecmwf-par",
        repo_type="dataset",
        allow_patterns="cmorph_thresholds/*.nc",
        local_dir=str(output_dir.parent),
    )
    print(f"  Saved to: {output_dir}")


def download_enso_iod(output_dir: Path) -> None:
    import urllib.request

    output_dir.mkdir(parents=True, exist_ok=True)
    dest = output_dir / "enso_iod_index.csv"
    if dest.exists():
        print(f"  Already exists: {dest}")
        return

    print("  Downloading ENSO/IOD index …")
    # Bundled index from HuggingFace dataset
    url = "https://huggingface.co/datasets/E4DRR/gik-ecmwf-par/resolve/main/enso_iod_index.csv"
    urllib.request.urlretrieve(url, dest)
    print(f"  Saved: {dest}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Download GIK-IceChain reference data")
    parser.add_argument("--component", choices=["all", "admin", "thresholds", "enso_iod"],
                        default="all")
    parser.add_argument("--output", type=Path, default=REPO_ROOT / "data",
                        help="Base output directory")
    args = parser.parse_args()

    comp = args.component
    base = Path(args.output)

    print(f"Downloading: {comp}  →  {base}")

    if comp in ("all", "admin"):
        download_admin_boundaries(base / "admin_boundaries")

    if comp in ("all", "thresholds"):
        download_cmorph_thresholds(base / "cmorph_thresholds")

    if comp in ("all", "enso_iod"):
        download_enso_iod(base)

    print("Done.")


if __name__ == "__main__":
    main()
