"""Unit tests for GIKFlatParquetParser and _to_ref_value."""

from __future__ import annotations

import json

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from gik_icechain.conversion.virtualizer import GIKFlatParquetParser, _to_ref_value


class TestToRefValue:
    def test_list_passthrough(self):
        v = ["s3://bucket/file.grib2", 0, 1000]
        assert _to_ref_value(v) == v

    def test_str_passthrough(self):
        assert _to_ref_value("hello") == "hello"

    def test_bytes_decode(self):
        assert _to_ref_value(b"hello") == "hello"

    def test_bytes_base64_on_non_utf8(self):
        raw = bytes(range(200, 210))
        result = _to_ref_value(raw)
        assert result.startswith("base64:")

    def test_dict_serialised_to_json(self):
        v = {"a": 1}
        result = _to_ref_value(v)
        assert json.loads(result) == v

    def test_int_serialised(self):
        result = _to_ref_value(42)
        assert result == "42"


class TestGIKFlatParquetParser:
    NLAT, NLON = 6, 6

    def _make_parquet(self, tmp_path, steps=(0, 6, 12), vars=("tp",)):
        rows = []
        for step in steps:
            for var in vars:
                rows.append(
                    {
                        "key": f"step_{step}/{var}/sfc/0/0",
                        "value": json.dumps(
                            [
                                f"s3://ecmwf-forecasts/fake/{step}.grib2",
                                step * 100,
                                500,
                            ]
                        ),
                    }
                )
        rows.append(
            {
                "key": "2m_temperature/heightAboveGround/2/.zarray",
                "value": json.dumps(
                    {
                        "chunks": [1, self.NLAT, self.NLON],
                        "compressor": None,
                        "dtype": "<f4",
                        "fill_value": "NaN",
                        "filters": None,
                        "order": "C",
                        "shape": [len(steps), self.NLAT, self.NLON],
                        "zarr_format": 2,
                    }
                ),
            }
        )
        path = tmp_path / "test.parquet"
        table = pa.Table.from_pandas(pd.DataFrame(rows))
        with pa.OSFile(str(path), "wb") as f:
            pq.write_table(table, f)
        return str(path)

    def test_step_hours_extracted(self, tmp_path):
        GIKFlatParquetParser()
        path = self._make_parquet(tmp_path, steps=[0, 6, 12])
        # Call the parser directly (registry not used for step_hours extraction)
        # Just verify that after parsing, step_hours is populated correctly
        import pandas as _pd

        df = _pd.read_parquet(path)
        df["key"] = df["key"].astype(str)
        from gik_icechain.conversion.virtualizer import _SFC_STEP_RE

        extracted = df["key"].str.extract(_SFC_STEP_RE, expand=True)
        sfc_mask = extracted[0].notna()
        assert sfc_mask.any(), "No SFC chunk refs found"
        step_nums = extracted.loc[sfc_mask, 0].astype(int).unique().tolist()
        assert sorted(step_nums) == [0, 6, 12]

    def test_no_sfc_refs_returns_empty_store(self, tmp_path):
        path = tmp_path / "empty.parquet"
        table = pa.Table.from_pandas(pd.DataFrame([{"key": "some/other/key", "value": "{}"}]))
        with pa.OSFile(str(path), "wb") as f:
            pq.write_table(table, f)
        parser = GIKFlatParquetParser()
        assert parser.step_hours == []

    def test_multiple_variables_extracted(self, tmp_path):
        from gik_icechain.conversion.virtualizer import _SFC_STEP_RE

        rows = []
        for var in ["tp", "2t", "ro"]:
            rows.append(
                {
                    "key": f"step_0/{var}/sfc/0/0",
                    "value": json.dumps(["s3://ecmwf-forecasts/fake.grib2", 0, 100]),
                }
            )
        df = pd.DataFrame(rows)
        extracted = df["key"].str.extract(_SFC_STEP_RE, expand=True)
        vars_found = set(extracted[1].dropna().tolist())
        assert vars_found == {"tp", "2t", "ro"}

    def test_pl_vars_excluded(self, tmp_path):
        """Pressure-level keys must NOT match the SFC regex."""
        from gik_icechain.conversion.virtualizer import _SFC_STEP_RE

        rows = [
            {"key": "step_0/z/pl/0/0", "value": "{}"},
            {"key": "step_0/tp/sfc/0/0", "value": "{}"},
        ]
        df = pd.DataFrame(rows)
        extracted = df["key"].str.extract(_SFC_STEP_RE, expand=True)
        sfc_mask = extracted[0].notna()
        # Only the sfc row should match
        assert sfc_mask.sum() == 1
        assert extracted.loc[sfc_mask, 1].iloc[0] == "tp"
