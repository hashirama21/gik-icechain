#!/usr/bin/env python3
"""Validate IceChunk store integrity: committed days, gaps, variable presence.

Usage:
    python scripts/validate_store.py --store-uri s3://your-bucket/gik-icechain-store
    python scripts/validate_store.py --store-uri /local/path/to/store
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate IceChunk store integrity")
    parser.add_argument("--store-uri", required=True,
                        help="IceChunk store URI (s3:// or local path)")
    parser.add_argument("--json", action="store_true",
                        help="Output results as JSON")
    args = parser.parse_args()

    from gik_icechain.conversion.icechunk_writer import IceChainStore

    store = IceChainStore(args.store_uri)
    store.create_or_open()
    report = store.validate()

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"Store URI       : {args.store_uri}")
        print(f"Committed days  : {report['committed_days']}")
        print(f"Date range      : {report['date_range']}")
        print(f"Total snapshots : {report['total_snapshots']}")
        print(f"Gaps detected   : {report['gaps_detected']}")
        if report["gap_details"]:
            print("Gap details:")
            for g in report["gap_details"]:
                print(f"  {g}")
        print(f"Variables       : {report['variables_present']}")

    if report["gaps_detected"] > 0:
        print(f"\nWARNING: {report['gaps_detected']} gap(s) detected", file=sys.stderr)
        sys.exit(1)

    print("\nStore is valid.")


if __name__ == "__main__":
    main()