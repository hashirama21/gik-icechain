"""Pipeline configuration backed by configs/default.yaml.

Loads and validates the YAML config using Pydantic Settings.
Use ``load_config()`` to obtain a ``GIKConfig`` instance; downstream
modules should import the config object rather than reading YAML directly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict
import yaml


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


class OutputsConfig(BaseModel):
    icechunk_store_uri: str = ""
    exceedance_store_uri: str = ""
    risk_output_dir: str = "results/admin1_risk/"
    dashboard_data_dir: str = "dashboard/calendar_map/data/"


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
    variables: list[str] = Field(
        default_factory=lambda: ["tp", "2t", "10u", "10v", "sro", "ssro"]
    )
    icechunk: IceChunkConfig = Field(default_factory=IceChunkConfig)
    gap_fill: GapFillConfig = Field(default_factory=GapFillConfig)


class ThresholdsConfig(BaseModel):
    adaptive: bool = True
    enso_iod_index_path: str = "data/enso_iod_index.csv"
    fit_from_cmorph: bool = False


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
        default_factory=lambda: {"time": 10, "lat": 100, "lon": 100}
    )


class Component2Config(BaseModel):
    windows_h: list[int] = Field(default_factory=lambda: [3, 6, 12, 24, 48, 72, 168])
    return_periods: list[int] = Field(default_factory=lambda: [2, 5, 10, 20, 40, 100])
    thresholds: ThresholdsConfig = Field(default_factory=ThresholdsConfig)
    aifs_track: AIFSTrackConfig = Field(default_factory=AIFSTrackConfig)
    dask: DaskConfig = Field(default_factory=DaskConfig)
    output_chunks: dict[str, Any] = Field(
        default_factory=lambda: {
            "date": 30, "lat": 100, "lon": 100, "window": -1, "return_period": -1
        }
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
    emdat_refinement: EMDATRefinementConfig = Field(default_factory=EMDATRefinementConfig)
    aggregation: AggregationConfig = Field(default_factory=AggregationConfig)
    output: Component3OutputConfig = Field(default_factory=Component3OutputConfig)


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


class LoggingConfig(BaseModel):
    level: str = "INFO"
    format: str = "json"
    output: str = "stderr"


class GIKConfig(BaseSettings):
    sources: SourcesConfig = Field(default_factory=SourcesConfig)
    outputs: OutputsConfig = Field(default_factory=OutputsConfig)
    component1: Component1Config = Field(default_factory=Component1Config)
    component2: Component2Config = Field(default_factory=Component2Config)
    component3: Component3Config = Field(default_factory=Component3Config)
    dashboard: DashboardConfig = Field(default_factory=DashboardConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    model_config = SettingsConfigDict(
        yaml_file=str(_DEFAULT_CONFIG),
        yaml_file_encoding="utf-8",
        extra="ignore",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        from pydantic_settings.main import YamlConfigSettingsSource
        return (init_settings, YamlConfigSettingsSource(settings_cls))


def load_config(path: Path | None = None) -> GIKConfig:
    """Load and validate the pipeline configuration from a YAML file.

    Args:
        path: Path to a YAML config file. Defaults to configs/default.yaml.

    Returns:
        Validated GIKConfig instance.
    """
    if path is None:
        return GIKConfig()

    raw = yaml.safe_load(path.read_text())
    return GIKConfig.model_validate(raw)
