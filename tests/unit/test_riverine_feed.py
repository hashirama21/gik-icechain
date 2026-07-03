"""Unit tests for the upstream-basin riverine feed (roadmap Item 1, data side)."""

from pathlib import Path

import pandas as pd
import pytest

from gik_icechain.risk.riverine import load_upstream_map, pool_upstream_ratio

_MAP_PATH = Path("data/river_basins/upstream_admin1.yaml")


class TestPoolUpstreamRatio:
    def test_downstream_pools_upstream_max(self):
        ratio = pd.Series({"ETH_Oromia": 1.2, "ETH_Somali": 0.5, "SOM_Middle Shebelle": 0.0})
        umap = {"SOM_Middle Shebelle": ["ETH_Oromia", "ETH_Somali"]}
        out = pool_upstream_ratio(ratio, umap, attenuation=1.0)
        assert out["SOM_Middle Shebelle"] == pytest.approx(1.2)

    def test_attenuation_applied(self):
        ratio = pd.Series({"ETH_Oromia": 1.0, "SOM_Hiiraan": 0.0})
        out = pool_upstream_ratio(ratio, {"SOM_Hiiraan": ["ETH_Oromia"]}, attenuation=0.9)
        assert out["SOM_Hiiraan"] == pytest.approx(0.9)

    def test_mean_aggregate(self):
        ratio = pd.Series({"A": 1.0, "B": 0.0, "D": 0.0})
        out = pool_upstream_ratio(ratio, {"D": ["A", "B"]}, attenuation=1.0, aggregate="mean")
        assert out["D"] == pytest.approx(0.5)

    def test_missing_upstream_skipped(self):
        ratio = pd.Series({"ETH_Oromia": 1.0, "SOM_Gedo": 0.0})
        # ETH_SNNPR absent from the series → skipped, not an error.
        out = pool_upstream_ratio(ratio, {"SOM_Gedo": ["ETH_Oromia", "ETH_SNNPR"]}, attenuation=1.0)
        assert out["SOM_Gedo"] == pytest.approx(1.0)

    def test_unit_not_in_map_is_zero(self):
        ratio = pd.Series({"KEN_Nairobi": 0.7})
        out = pool_upstream_ratio(ratio, {"SOM_Gedo": ["ETH_Oromia"]}, attenuation=0.9)
        assert out["KEN_Nairobi"] == 0.0

    def test_no_upstream_signal_gives_zero(self):
        ratio = pd.Series({"ETH_Oromia": 0.0, "SOM_Hiiraan": 0.0})
        out = pool_upstream_ratio(ratio, {"SOM_Hiiraan": ["ETH_Oromia"]}, attenuation=0.9)
        assert out["SOM_Hiiraan"] == 0.0


class TestUpstreamMap:
    def test_curated_map_loads_and_covers_key_basins(self):
        umap = load_upstream_map(_MAP_PATH)
        # The Somalia units C3 missed must be present with Ethiopian headwaters.
        for downstream in ("SOM_Middle Shebelle", "SOM_Lower Juba"):
            assert downstream in umap
            assert any(u.startswith("ETH_") for u in umap[downstream])

    def test_map_shape(self):
        umap = load_upstream_map(_MAP_PATH)
        assert all(isinstance(v, list) and v for v in umap.values())


class TestConfigLoads:
    def test_default_config_validates_with_riverine(self):
        from gik_icechain.shared.config import load_config

        cfg = load_config()
        assert cfg.component3.riverine.enabled is True
        assert cfg.component3.crma_model.riverine_aware_hazard is True
        assert cfg.component3.crma_model.cost_loss.severity_red_split == pytest.approx(0.30)
        assert cfg.component3.crma_model.severity.enabled is True
