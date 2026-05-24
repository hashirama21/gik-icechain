#!/usr/bin/env python3
"""Export GIK-IceChain admin-1 risk GeoJSON to East Africa Hazard Watch Portal format.

Usage:
    python scripts/export_eahw.py \\
        --risk-dir results/admin1_risk/ \\
        --output   results/eahw_export/
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Export risk GeoJSON to EAHW Portal format")
    parser.add_argument("--risk-dir", type=Path, required=True,
                        help="Directory containing per-day risk GeoJSON files")
    parser.add_argument("--output", type=Path, required=True,
                        help="Output directory for EAHW-formatted GeoJSON files")
    args = parser.parse_args()

    from gik_icechain.risk.geojson_writer import export_eahw_format

    risk_files = sorted(Path(args.risk_dir).glob("*_admin1_risk.geojson"))
    if not risk_files:
        print(f"No risk GeoJSON files found in: {args.risk_dir}", file=sys.stderr)
        sys.exit(1)

    args.output.mkdir(parents=True, exist_ok=True)
    exported = 0
    for f in risk_files:
        date_str = f.stem[:10]
        out_path = args.output / f"eahw_{date_str}.geojson"
        export_eahw_format(f, out_path)
        exported += 1

    print(f"Exported {exported} files to {args.output}")


if __name__ == "__main__":
    main()
