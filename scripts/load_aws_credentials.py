"""Load AWS credentials from the access-key CSV at the repo root.

Import ``load_aws_credentials()`` (notebooks, scripts) to set the AWS_*
environment variables in-process, or run as a script to print the shell
export commands:

    python scripts/load_aws_credentials.py            # POSIX exports
    python scripts/load_aws_credentials.py --powershell
"""

from __future__ import annotations

import csv
import os
import sys
from pathlib import Path

DEFAULT_CSV = Path(__file__).resolve().parents[1] / "develop_accessKeys.csv"
DEFAULT_REGION = "eu-north-1"


def load_aws_credentials(
    csv_path: Path | str = DEFAULT_CSV,
    region: str = DEFAULT_REGION,
) -> dict[str, str]:
    """Read the AWS console key export CSV and set AWS_* env vars.

    Returns the mapping that was applied.
    """
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found - export your access keys from the AWS console "
            "and place the CSV at the repo root (it is gitignored)."
        )
    with path.open(newline="", encoding="utf-8-sig") as f:
        row = next(csv.DictReader(f))
    normalized = {k.strip().lower(): v.strip() for k, v in row.items() if k and v}
    access_key = normalized.get("access key id")
    secret_key = normalized.get("secret access key")
    if not access_key or not secret_key:
        raise ValueError(
            f"{path} must have 'Access key ID' and 'Secret access key' columns"
        )
    env = {
        "AWS_ACCESS_KEY_ID": access_key,
        "AWS_SECRET_ACCESS_KEY": secret_key,
        "AWS_REGION": region,
        "AWS_DEFAULT_REGION": region,
    }
    os.environ.update(env)
    return env


def main() -> int:
    env = load_aws_credentials()
    powershell = "--powershell" in sys.argv
    for key, value in env.items():
        if powershell:
            print(f'$env:{key} = "{value}"')
        else:
            print(f'export {key}="{value}"')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
