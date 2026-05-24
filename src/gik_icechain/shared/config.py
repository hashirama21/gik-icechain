"""Pipeline configuration loaded via OmegaConf + validated by Pydantic.

OmegaConf handles YAML loading and CLI-override composition (via Hydra or
manual merge); Pydantic validates types and supplies defaults.

Usage:
    cfg = load_config()                          # load configs/default.yaml
    cfg = load_config(Path("my_override.yaml"))  # merge on top of defaults
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

_DEFAULT_CONFIG = Path(__file__).parents[4] / "configs" / "default.yaml"


class SourcesConfig(BaseModel):
    gik_hf_dataset: str = "E4DRR/gik-ecmwf-par"
    gik_catalog_file: str = "catalog.parquet"
    ecmwf_s3_bucket: str = "ecmwf-forecasts"
    ecmwf_s3_region: str = "eu-west-1"
    ecmwf_s3_no_sign: bool = True
    cmorph_thresholds_path: str = "data/cmorph_thresholds/"
    gpm_imerg_path: str = "data/gpm_imerg/"
    emdat_path: str = "data/emdat/east_africa_floods.csv"
    admin_boundaries_path: str = "data/admin_boundaries/east_africa_admin1.gpkg"


# Outputs

class OutputsConfig(BaseModel):
    icechunk_store_uri: str = ""
    exceedance_store_uri: str = ""
    risk_output_dir: str = "results/admin1_risk/"
    dashboard_data_dir: str = "dashboard/calendar_map/data/"


#  Component 1

class IceChunkConfig(BaseModel):
    branch: str = "main"
    commit_message_template: str = "GIK ingest: {date}T{run_hour:02d}Z"
    tag_format: str = "{date}T{run_hour:02d}Z"


class GapFillConfig(BaseModel):
    enabled: bool = True
    cloud_run_job: str = "deploy/cloud_run/job_c1.yaml"
    lithops_config: str = "deploy/cloud_run/lithops_config.yaml"
    workers: int = 50


class Component1Config(BaseModel):
    run_hours: list[int] = Field(default_factory=lambda: [0])
    variables: list[str] = Field(default_factory=lambda: ["tp", "2t", "10u", "10v", "ro"])
    icechunk: IceChunkConfig = Field(default_factory=IceChunkConfig)
    gap_fill: GapFillConfig = Field(default_factory=GapFillConfig)


#  Component 2

class ThresholdsConfig(BaseModel):
    adaptive: bool = True
    enso_iod_index_path: str = "data/enso_iod_index.csv"
    fit_from_cmorph: bool = False
    enso_nino34_threshold: float = 0.5   # °C Niño-3.4 for El Niño / La Niña
    iod_dmi_threshold: float = 0.4       # °C DMI for Positive / Negative IOD


class AIFSTrackConfig(BaseModel):
    enabled: bool = True
    aifs_store_uri: str = ""


class DaskConfig(BaseModel):
    scheduler: str = "distributed"
    n_workers: int = 16
    threads_per_worker: int = 2
    memory_limit: str = "8GB"
    dashboard_address: str = ":8787"
    chunk_dims: dict[str, Any] = Field(
        default_factory=lambda: {"member": -1, "step": -1, "latitude": 50, "longitude": 50}
    )


class Component2Config(BaseModel):
    windows_h: list[int] = Field(default_factory=lambda: [3, 6, 12, 24, 48, 72, 168])
    return_periods: list[int] = Field(default_factory=lambda: [2, 5, 10, 20, 40, 100])
    thresholds: ThresholdsConfig = Field(default_factory=ThresholdsConfig)
    aifs_track: AIFSTrackConfig = Field(default_factory=AIFSTrackConfig)
    dask: DaskConfig = Field(default_factory=DaskConfig)
    output_chunks: dict[str, Any] = Field(
        default_factory=lambda: {
            "date": 30,
            "latitude": 100,
            "longitude": 100,
            "window": -1,
            "return_period": -1,
        }
    )


#  Component 3 — CRMA model parameters

class CompoundScoreThresholdsConfig(BaseModel):
    fresh: list[float] = Field(default_factory=lambda: [1.5, 4.0, 7.0])
    prolonged: list[float] = Field(default_factory=lambda: [1.0, 3.0, 6.0])


class CompoundCPTBucketSetConfig(BaseModel):
    low: list[float] = Field(default_factory=lambda: [0.85, 0.12, 0.02, 0.01])
    mid: list[float] = Field(default_factory=lambda: [0.20, 0.60, 0.15, 0.05])
    mod: list[float] = Field(default_factory=lambda: [0.05, 0.20, 0.55, 0.20])
    high: list[float] = Field(default_factory=lambda: [0.02, 0.08, 0.30, 0.60])


class CompoundCPTBucketsConfig(BaseModel):
    fresh: CompoundCPTBucketSetConfig = Field(default_factory=CompoundCPTBucketSetConfig)
    prolonged: CompoundCPTBucketSetConfig = Field(
        default_factory=lambda: CompoundCPTBucketSetConfig(
            low=[0.75, 0.18, 0.05, 0.02],
            mid=[0.10, 0.45, 0.30, 0.15],
            mod=[0.02, 0.13, 0.45, 0.40],
            high=[0.01, 0.04, 0.20, 0.75],
        )
    )


class ClusterWeightConfig(BaseModel):
    forecast: float = 2.0
    obs: float = 1.5
    api: float = 1.5


class CRMAModelConfig(BaseModel):
    """All CRMA BN parameters — no hardcoded values in source modules."""

    # Evidence discretization thresholds
    gpm_obs_normal_mmday: float = 5.0
    gpm_obs_above_mmday: float = 25.0
    api_threshold_normal_mm: float = 30.0
    api_threshold_saturated_mm: float = 80.0
    spatial_threshold_regional: float = 0.25
    spatial_threshold_extensive: float = 0.75
    consecutive_signal_threshold: int = 3
    soil_memory_days: int = 7       # days of saturation → SoilMemory_State=1

    # Exceedance signal thresholds
    signal_threshold_prob: float = 0.15
    rp_signal: int = 5

    # CPT parameters
    compound_score_thresholds: CompoundScoreThresholdsConfig = Field(
        default_factory=CompoundScoreThresholdsConfig
    )
    compound_cpt_buckets: CompoundCPTBucketsConfig = Field(
        default_factory=CompoundCPTBucketsConfig
    )
    cluster_weights: dict[str, ClusterWeightConfig] = Field(
        default_factory=lambda: {
            "equatorial_east": ClusterWeightConfig(forecast=2.0, obs=1.5, api=1.5),
            "horn_arid": ClusterWeightConfig(forecast=2.5, obs=1.0, api=2.0),
            "great_rift": ClusterWeightConfig(forecast=2.0, obs=1.5, api=1.8),
            "nile_basin": ClusterWeightConfig(forecast=1.8, obs=2.0, api=1.5),
        }
    )
    # API_State inter-slice DBN transition matrix (rows: API_t, cols: API_{t-1})
    api_transition: list[list[float]] = Field(
        default_factory=lambda: [
            [0.70, 0.20, 0.05],
            [0.25, 0.55, 0.35],
            [0.05, 0.25, 0.60],
        ]
    )


class CRMAConfig(BaseModel):
    cpt_path: str | None = None
    use_refined_cpts: bool = False


class APIConfig(BaseModel):
    enabled: bool = True
    decay_factor: float = 0.8
    initial_api_mm: float = 20.0


class EMDATRefinementConfig(BaseModel):
    enabled: bool = True
    laplace_alpha: float = 1.0
    negative_sample_ratio: float = 3.0
    output_cpt_path: str = "results/validation/refined_cpts.json"


class AggregationConfig(BaseModel):
    method: str = "mean"
    min_coverage_fraction: float = 0.5


class Component3OutputConfig(BaseModel):
    geojson: bool = True
    zarr: bool = True
    eahw_export: bool = True


class Component3Config(BaseModel):
    crma: CRMAConfig = Field(default_factory=CRMAConfig)
    api: APIConfig = Field(default_factory=APIConfig)
    crma_model: CRMAModelConfig = Field(default_factory=CRMAModelConfig)
    emdat_refinement: EMDATRefinementConfig = Field(default_factory=EMDATRefinementConfig)
    aggregation: AggregationConfig = Field(default_factory=AggregationConfig)
    output: Component3OutputConfig = Field(default_factory=Component3OutputConfig)


# Dashboard

class TiTilerConfig(BaseModel):
    endpoint: str = ""
    cog_bucket: str = ""


class CalendarMapConfig(BaseModel):
    signal_threshold_yellow: float = 0.15
    signal_threshold_orange: float = 0.30
    signal_threshold_red: float = 0.50


class VedaUIConfig(BaseModel):
    base_url: str = ""


class DashboardConfig(BaseModel):
    titiler: TiTilerConfig = Field(default_factory=TiTilerConfig)
    calendar_map: CalendarMapConfig = Field(default_factory=CalendarMapConfig)
    veda_ui: VedaUIConfig = Field(default_factory=VedaUIConfig)


#  Logging

class LoggingConfig(BaseModel):
    level: str = "INFO"
    format: str = "json"
    output: str = "stderr"


#  Root config

class GIKConfig(BaseModel):
    sources: SourcesConfig = Field(default_factory=SourcesConfig)
    outputs: OutputsConfig = Field(default_factory=OutputsConfig)
    component1: Component1Config = Field(default_factory=Component1Config)
    component2: Component2Config = Field(default_factory=Component2Config)
    component3: Component3Config = Field(default_factory=Component3Config)
    dashboard: DashboardConfig = Field(default_factory=DashboardConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)


def load_config(path: Path | None = None) -> GIKConfig:
    """Load and validate the pipeline configuration via OmegaConf + Pydantic.

    Loads ``configs/default.yaml`` first, then deep-merges any override file
    on top.  The merged dict is validated by Pydantic before returning.

    Args:
        path: Optional override YAML file merged on top of defaults.

    Returns:
        Validated GIKConfig instance.
    """
    from omegaconf import OmegaConf

    base_cfg = OmegaConf.load(_DEFAULT_CONFIG)

    if path is not None and path.exists():
        override = OmegaConf.load(path)
        merged = OmegaConf.merge(base_cfg, override)
    else:
        merged = base_cfg

    return GIKConfig.model_validate(OmegaConf.to_container(merged, resolve=True))
