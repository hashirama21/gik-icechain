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

from pydantic import BaseModel, Field, field_validator, model_validator

DEFAULT_CONFIG_PATH = Path(__file__).parents[3] / "configs" / "default.yaml"


def _validate_windows_h(v: list[int]) -> list[int]:
    if any(w <= 0 for w in v):
        raise ValueError("All windows_h values must be > 0")
    if v != sorted(v):
        raise ValueError("windows_h must be sorted in ascending order")
    return v


class SourcesConfig(BaseModel):
    gik_hf_dataset: str = "E4DRR/gik-ecmwf-par"
    gik_catalog_file: str = "catalog.parquet"
    ecmwf_s3_bucket: str = "ecmwf-forecasts"
    ecmwf_s3_region: str = "eu-central-1"
    ecmwf_s3_no_sign: bool = True
    gpm_imerg_path: str = "data/gpm_imerg/"
    emdat_path: str = "data/emdat/east_africa_floods.csv"
    emdat_pcode_aliases_path: str = "data/emdat/pcode_aliases.csv"
    admin_boundaries_path: str = "data/admin_boundaries/east_africa_admin1.gpkg"


# Outputs


class OutputsConfig(BaseModel):
    icechunk_store_uri: str = ""
    icechunk_store_region: str = "eu-west-1"
    endpoint_url: str = ""  # S3-compatible endpoint (MinIO); empty = default AWS
    exceedance_store_uri: str = ""
    exceedance_icechunk_uri: str = ""
    risk_icechunk_uri: str = ""
    # S3 by default so C3 writes risk JSON (+ boundaries + checkpoint) straight to
    # object storage (no local disk in prod). Override with a local dir for dev.
    risk_output_dir: str = "s3://gik-icechain/admin1_risk"
    dashboard_data_dir: str = "dashboard/web/public/data/"


#  Component 1


class IceChunkConfig(BaseModel):
    branch: str = "main"
    commit_message_template: str = "GIK ingest: {date}T{run_hour:02d}Z"
    tag_format: str = "{date}T{run_hour:02d}Z"
    # Manifest splitting (IceChunk 2.x) - caps manifest fragment size so append
    # latency and metadata scan stay flat as the archive grows to 1200+ days.
    # Splits every array along the given dimension every `manifest_split_size`
    # index positions. See VirtualiZarr #884 (manifest-splitting recommendation).
    manifest_splitting: bool = True
    manifest_split_dim: str = "step"  # forecast-horizon axis (time-like)
    manifest_split_size: int = 1000


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
    enso_nino34_threshold: float = 0.5  # °C Niño-3.4 for El Niño / La Niña
    iod_dmi_threshold: float = 0.4  # °C DMI for Positive / Negative IOD
    cmorph_path: str = "data/cmorph_thresholds/"
    cmorph_hf_dataset: str = "E4DRR/virtualizarr-stores"
    min_samples_for_gev: int = 30
    fallback_strategy: str = "neutral"  # "neutral" | "climatology"
    fallback_rate_warning_threshold: float = 0.20


class AIFSTrackConfig(BaseModel):
    enabled: bool = True
    aifs_store_uri: str = ""
    exceedance_store_uri: str = ""
    variables: list[str] = Field(default_factory=lambda: ["tp"])
    run_hours: list[int] = Field(default_factory=lambda: [0])
    max_step_h: int = 360
    step_resolution_h: int = 6
    n_members: int = 51
    comparison_enabled: bool = True
    comparison_output_dir: str = "results/aifs_comparison/"

    @field_validator("run_hours")
    @classmethod
    def _check_run_hours(cls, v: list[int]) -> list[int]:
        valid = {0, 12}
        invalid = [h for h in v if h not in valid]
        if invalid:
            raise ValueError(f"AIFS ENS only runs at 0z and 12z, got invalid run_hours: {invalid}")
        return v


class SpatialConfig(BaseModel):
    bbox: list[float] | None = Field(
        default_factory=lambda: [-12.0, 23.0, 22.0, 52.0],
    )
    lat_dim: str = "latitude"
    lon_dim: str = "longitude"

    @property
    def lat_slice(self) -> slice | None:
        if self.bbox is None:
            return None
        return slice(self.bbox[1], self.bbox[0])  # descending lat

    @property
    def lon_slice(self) -> slice | None:
        if self.bbox is None:
            return None
        return slice(self.bbox[2], self.bbox[3])

    @property
    def as_tuple(self) -> tuple[float, float, float, float] | None:
        if self.bbox is None:
            return None
        return (self.bbox[0], self.bbox[1], self.bbox[2], self.bbox[3])


class DaskConfig(BaseModel):
    scheduler: str = "distributed"
    n_workers: int = 4
    threads_per_worker: int = 2
    memory_limit: str = "4GB"  # fallback when memory_fraction is null
    memory_fraction: float | None = 0.6  # cluster-wide share of total RAM; null = absolute
    dashboard_address: str = ":8787"
    chunk_dims: dict[str, Any] = Field(
        default_factory=lambda: {"member": 1, "step": 1, "latitude": 50, "longitude": 50}
    )


class ByteRangeCoalescingConfig(BaseModel):
    enabled: bool = True
    max_gap_bytes: int = 65536  # 64 KB
    max_merged_bytes: int = 5242880  # 5 MB


class ManifestAwareConfig(BaseModel):
    enabled: bool = True
    fetch_workers: int = Field(default=8, ge=1, le=64)
    min_members: int = Field(default=10, ge=1)


class ParallelConfig(BaseModel):
    max_workers: int | None = None  # None = auto (os.cpu_count())
    multiprocessing: bool = True
    day_timeout_s: int = 300
    mem_gb_per_worker: float = 3.0  # 0 disables the memory-based worker cap
    mem_headroom_fraction: float = 0.1  # share of total RAM kept free

    @field_validator("max_workers")
    @classmethod
    def _max_workers_positive(cls, v: int | None) -> int | None:
        if v is not None and v < 1:
            raise ValueError(f"max_workers must be >= 1, got {v}")
        return v


class WindowProfileConfig(BaseModel):
    windows_h: list[int]
    return_periods: list[int]
    max_forecast_h: int

    @field_validator("windows_h")
    @classmethod
    def _check_windows_h(cls, v: list[int]) -> list[int]:
        return _validate_windows_h(v)


class Component2Config(BaseModel):
    #  Dimension 1: Variables
    compute_variables: list[str] = Field(default_factory=lambda: ["tp"])
    risk_evidence_variables: list[str] = Field(
        default_factory=lambda: ["2t", "10u", "10v", "ro"],
    )
    # ECMWF IFS `tp` is decoded in metres; CMORPH GEV thresholds are in mm.
    # Scale the precip variable to mm before accumulation/exceedance so the
    # comparison is unit-consistent. Default 1000.0 (m -> mm); set 1.0 if the
    # source is already mm.
    precip_scale_to_mm: float = 1000.0

    # Flood-relevance floor (mm) per window: raises the effective GEV threshold
    # so arid cells with near-zero thresholds don't false-alarm.
    flood_floor_mm: dict[int, float] = Field(
        default_factory=lambda: {
            3: 15.0,
            6: 20.0,
            12: 25.0,
            24: 30.0,
            48: 40.0,
            72: 50.0,
            168: 75.0,
        }
    )

    #  Dimension 2: Steps
    step_resolution_h: int = 6
    max_forecast_h: int | None = None  # None = auto (max of active windows_h)
    step_buffer: int = 1
    # When a requested accumulation window is finer than the loaded step
    # resolution (e.g. a 3 h window on 6-hourly data): True = skip the window
    # with a warning (graceful), False = raise an error and abort the day.
    skip_subresolution_windows: bool = True

    # Possible-worlds tail signal: the upper member-quantile of accumulation /
    # GEV threshold, persisted as the `tail_ratio` variable alongside
    # `exceedance_prob` and consumed by the tail-aware Forecast_Hazard node.
    # 0.95 = the worst plausible ensemble member (cf. Nairobi flash-flood tail).
    tail_quantile: float = 0.95
    tail_enabled: bool = True

    #  Dimension 3 + 7: Windows (direct + profiles)
    windows_h: list[int] = Field(default_factory=lambda: [3, 6, 12, 24, 48, 72, 168])
    return_periods: list[int] = Field(default_factory=lambda: [2, 5, 10, 20, 40, 100])
    active_profile: str | None = None  # None = use top-level windows_h/return_periods
    window_profiles: dict[str, WindowProfileConfig] = Field(
        default_factory=lambda: {
            "flash_flood": WindowProfileConfig(
                windows_h=[3, 6, 12],
                return_periods=[2, 5, 10],
                max_forecast_h=24,
            ),
            "medium_range": WindowProfileConfig(
                windows_h=[24, 48, 72],
                return_periods=[5, 10, 20],
                max_forecast_h=72,
            ),
            "full": WindowProfileConfig(
                windows_h=[3, 6, 12, 24, 48, 72, 168],
                return_periods=[2, 5, 10, 20, 40, 100],
                max_forecast_h=168,
            ),
        }
    )

    #  Dimension 4: Spatial
    spatial: SpatialConfig = Field(default_factory=SpatialConfig)

    #  Dimension 5: Byte-range coalescing
    byte_range_coalescing: ByteRangeCoalescingConfig = Field(
        default_factory=ByteRangeCoalescingConfig,
    )

    #  Manifest-aware loading (IceChunk VirtualChunkRef path)
    manifest_aware: ManifestAwareConfig = Field(
        default_factory=ManifestAwareConfig,
    )

    #  Dimension 6: Parallelism
    parallel: ParallelConfig = Field(default_factory=ParallelConfig)

    #  Dimension 8: Thresholds
    thresholds: ThresholdsConfig = Field(default_factory=ThresholdsConfig)

    #  Other
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

    @field_validator("windows_h")
    @classmethod
    def _check_windows_h(cls, v: list[int]) -> list[int]:
        return _validate_windows_h(v)

    @model_validator(mode="after")
    def _validate_active_profile(self) -> Component2Config:
        if self.active_profile and self.active_profile not in self.window_profiles:
            available = list(self.window_profiles.keys())
            raise ValueError(
                f"active_profile '{self.active_profile}' not found in "
                f"window_profiles (available: {available})"
            )
        return self

    @property
    def effective_windows_h(self) -> list[int]:
        """Resolve windows from active profile or top-level config."""
        if self.active_profile and self.active_profile in self.window_profiles:
            return self.window_profiles[self.active_profile].windows_h
        return self.windows_h

    @property
    def effective_return_periods(self) -> list[int]:
        """Resolve return periods from active profile or top-level config."""
        if self.active_profile and self.active_profile in self.window_profiles:
            return self.window_profiles[self.active_profile].return_periods
        return self.return_periods

    @property
    def effective_max_forecast_h(self) -> int:
        """Resolve max forecast horizon from profile, explicit setting, or auto."""
        if self.active_profile and self.active_profile in self.window_profiles:
            return self.window_profiles[self.active_profile].max_forecast_h
        if self.max_forecast_h is not None:
            return self.max_forecast_h
        return max(self.effective_windows_h)

    @property
    def max_steps_needed(self) -> int:
        """Number of steps to load based on effective max forecast and resolution."""
        return (self.effective_max_forecast_h // self.step_resolution_h) + self.step_buffer + 1


#  Component 3 - CRMA model parameters


class CompoundScoreThresholdsConfig(BaseModel):
    fresh: list[float] = Field(default_factory=lambda: [1.5, 4.0, 7.0])
    prolonged: list[float] = Field(default_factory=lambda: [1.0, 3.0, 6.0])


class CompoundCPTBucketSetConfig(BaseModel):
    low: list[float] = Field(default_factory=lambda: [0.85, 0.12, 0.02, 0.01])
    mid: list[float] = Field(default_factory=lambda: [0.20, 0.60, 0.15, 0.05])
    mod: list[float] = Field(default_factory=lambda: [0.05, 0.20, 0.55, 0.20])
    high: list[float] = Field(default_factory=lambda: [0.02, 0.08, 0.30, 0.60])

    @model_validator(mode="after")
    def _buckets_sum_to_one(self) -> CompoundCPTBucketSetConfig:
        for name in ("low", "mid", "mod", "high"):
            probs = getattr(self, name)
            total = sum(probs)
            if abs(total - 1.0) > 1e-6:
                raise ValueError(f"CPT bucket '{name}' sums to {total:.6f}, expected 1.0")
        return self


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


class SoftEvidenceConfig(BaseModel):
    """Gaussian soft-binning of continuous BN evidence (virtual / soft evidence).

    When enabled, a near-threshold continuous observation is propagated as a
    probability distribution over the node's discrete states (Gaussian CDF
    differences at the bin edges) instead of being collapsed to a single hard
    state. The risk posterior becomes the weighted marginal over the lookup
    table: ``P(risk) = Σ_states Π_k p_k(state) · P(risk | states)``. As every
    ``sigma → 0`` this reduces *exactly* to the hard lookup path.

    Each sigma is a bandwidth in the node's own units (≈30% of the narrowest
    bin spacing, per the measurement-error / fuzzy-set rationale). Only the
    continuous-derived nodes are soft-binned; count/quality-derived nodes
    (Temporal_Persist, Data_Confidence, Soil_Memory) stay one-hot.
    """

    # Default OFF: on the April-2024 validation window (exc=0 misses, no
    # near-threshold evidence to rescue) soft-binning is net-conservative
    # (Yellow 105→94, Orange/Red flat). Theoretically sound and σ→0 == hard;
    # enable + demonstrate on a partial-signal (convective) window before
    # making it the production default. See docs/ISSUES.md A2 A/B.
    enabled: bool = False
    sigma_forecast: float = 0.05  # exceedance-probability units
    sigma_tail: float = 0.07  # tail-ratio units
    sigma_gpm: float = 5.0  # mm/day
    sigma_spatial: float = 0.08  # coverage fraction
    sigma_api: float = 10.0  # mm
    sigma_trend: float = 1.0  # mm/day (rainfall-trend slope)

    @model_validator(mode="after")
    def _sigmas_non_negative(self) -> SoftEvidenceConfig:
        for name in (
            "sigma_forecast",
            "sigma_tail",
            "sigma_gpm",
            "sigma_spatial",
            "sigma_api",
            "sigma_trend",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be >= 0 (0 reduces to the hard path)")
        return self


class CostLossConfig(BaseModel):
    """Cost-loss decision trigger (Murphy 1977; Coughlan de Perez et al. 2015).

    Instead of labelling each unit with the argmax risk state, assign the
    *highest* tier whose cumulative posterior probability exceeds that tier's
    cost-loss ratio C/L:

        Red    if P(≥Red)    ≥ tau_red
        Orange if P(≥Orange) ≥ tau_orange
        Yellow if P(≥Yellow) ≥ tau_yellow
        else Green

    For anticipatory action C/L is low (acting early is cheap, missing is
    devastating), so the optimal trigger probability is far below the argmax's
    implicit ~0.5 - this lifts recall / lead time at a controlled false-alarm
    cost. More expensive actions get a higher bar: tau_yellow ≤ tau_orange ≤
    tau_red. Defaults follow published forecast-based-financing C/L ratios
    (~0.1 cash transfers, ~0.2 pre-positioned stockpiles). Default OFF.
    """

    enabled: bool = False
    tau_yellow: float = 0.15
    tau_orange: float = 0.25
    tau_red: float = 0.35
    # Continuous-severity split for the Orange/Red boundary. When > 0 (and both
    # cost_loss and severity are enabled), a unit that qualifies for Red by the
    # cumulative posterior is demoted to Orange unless its severity_score reaches
    # this value. This decouples the tier from the saturated ``p_red`` (~0.39 for
    # every Red), which otherwise makes tau_orange == tau_red degenerate.
    # 0 = off (legacy cumulative-posterior tiering only).
    severity_red_split: float = 0.0

    @model_validator(mode="after")
    def _taus_ordered(self) -> CostLossConfig:
        if not (0.0 < self.tau_yellow <= self.tau_orange <= self.tau_red <= 1.0):
            raise ValueError(
                "cost-loss taus must satisfy 0 < tau_yellow <= tau_orange <= "
                f"tau_red <= 1 (got {self.tau_yellow}, {self.tau_orange}, {self.tau_red})"
            )
        if not (0.0 <= self.severity_red_split <= 1.0):
            raise ValueError(
                f"severity_red_split must be in [0, 1] (got {self.severity_red_split})"
            )
        return self


class SeverityGradationConfig(BaseModel):
    """Continuous severity score exposed alongside the discrete risk state.

    The discrete Risk_State CPT collapses continuous forecast magnitude into a
    handful of ``p_red`` values (empirically ~0.39 for every Red), so alerts of
    very different intensity are indistinguishable. ``severity_score`` is a
    monotone [0, 1] blend of the *continuous* evidence - max exceedance
    probability, ensemble tail ratio, spatial coverage and API saturation - that
    restores a within-tier hierarchy for ranking and dashboards. It is additive
    (does not change the risk label). Weights are normalised internally.
    """

    enabled: bool = True
    w_exceedance: float = 0.45
    w_tail: float = 0.25
    w_spatial: float = 0.20
    w_api: float = 0.10

    @model_validator(mode="after")
    def _weights_nonneg(self) -> SeverityGradationConfig:
        ws = (self.w_exceedance, self.w_tail, self.w_spatial, self.w_api)
        if any(w < 0 for w in ws) or sum(ws) <= 0:
            raise ValueError(f"severity weights must be non-negative and sum > 0 (got {ws})")
        return self


class CRMAModelConfig(BaseModel):
    """All CRMA BN parameters - no hardcoded values in source modules."""

    # Evidence discretization thresholds
    gpm_obs_normal_mmday: float = 5.0
    gpm_obs_above_mmday: float = 25.0
    api_threshold_normal_mm: float = 30.0
    api_threshold_saturated_mm: float = 80.0
    spatial_threshold_regional: float = 0.25
    spatial_threshold_extensive: float = 0.75
    consecutive_signal_threshold: int = 3
    soil_memory_days: int = 7  # days of saturation → SoilMemory_State=1

    # Forecast_Hazard discretization thresholds (exceedance prob)
    hazard_medium_threshold: float = 0.15  # Low → Medium boundary
    hazard_high_threshold: float = 0.40  # Medium → High boundary
    # Exceedance signal thresholds
    signal_threshold_prob: float = 0.15
    rp_signal: int = 5
    # Return periods the risk engine evaluates (risk_state produced per RP so the
    # dashboard can switch 2yr↔5yr). rp_signal is the primary/default one.
    rp_signal_options: list[int] = Field(default_factory=lambda: [2, 5])
    # Per-RP Forecast_Hazard boundaries (medium, high) - a 2yr threshold is
    # exceeded more often, so its boundaries sit higher than the 5yr ones.
    hazard_thresholds_by_rp: dict[int, tuple[float, float]] = Field(
        default_factory=lambda: {2: (0.30, 0.60), 5: (0.15, 0.40)}
    )
    hazard_extreme_threshold: float = 0.70  # High → Extreme boundary
    hazard_extreme_by_rp: dict[int, float] = Field(default_factory=lambda: {2: 0.85, 5: 0.70})

    # Tail-aware Forecast_Hazard (possible-worlds reasoning). When enabled, the
    # Forecast_Hazard state is max(fraction-based state, tail-based state) so a
    # narrow but extreme ensemble tail escalates hazard even when the
    # ensemble-mean exceedance fraction is ~0. Ratios are tail_ratio = pXX member
    # accumulation / GEV return level (see exceedance.compute_tail_ratio):
    #   ratio ≥ tail_medium_ratio  → Forecast_Hazard ≥ Medium
    #   ratio ≥ tail_high_ratio    → Forecast_Hazard ≥ High
    #   ratio ≥ tail_extreme_ratio → Forecast_Hazard = Extreme
    tail_aware_hazard: bool = True
    tail_medium_ratio: float = 0.80  # p95 member at ~80% of the return level
    tail_high_ratio: float = 1.00  # p95 member reaches the return level
    tail_extreme_ratio: float = 1.30  # p95 member well past the return level

    # Riverine-aware Forecast_Hazard: escalate on an upstream/riverine ratio
    # even when local exceedance is ~0. riverine_ratio defaults to 0, so units
    # without an upstream feed are unaffected.
    riverine_aware_hazard: bool = True
    riverine_medium_ratio: float = 0.80
    riverine_high_ratio: float = 1.00
    riverine_extreme_ratio: float = 1.30
    sigma_riverine: float = 0.07

    # Gaussian soft-binning of continuous evidence (virtual evidence). Disabled
    # → hard discretization (legacy). See SoftEvidenceConfig.
    soft_evidence: SoftEvidenceConfig = Field(default_factory=SoftEvidenceConfig)

    # Cost-loss decision trigger. Disabled → argmax labelling (legacy). When
    # enabled, the risk label is the highest tier whose cumulative posterior
    # exceeds that tier's cost-loss ratio. See CostLossConfig.
    cost_loss: CostLossConfig = Field(default_factory=CostLossConfig)

    # Continuous within-tier severity score (additive; does not change the
    # label). Restores event hierarchy lost to the discrete Risk_State CPT.
    severity: SeverityGradationConfig = Field(default_factory=SeverityGradationConfig)

    @model_validator(mode="after")
    def _tail_ratios_ordered(self) -> CRMAModelConfig:
        if not (0.0 < self.tail_medium_ratio < self.tail_high_ratio < self.tail_extreme_ratio):
            raise ValueError(
                "tail ratios must satisfy 0 < medium < high < extreme "
                f"(got {self.tail_medium_ratio}, {self.tail_high_ratio}, "
                f"{self.tail_extreme_ratio})"
            )
        if not (
            0.0
            < self.riverine_medium_ratio
            < self.riverine_high_ratio
            < self.riverine_extreme_ratio
        ):
            raise ValueError(
                "riverine ratios must satisfy 0 < medium < high < extreme "
                f"(got {self.riverine_medium_ratio}, {self.riverine_high_ratio}, "
                f"{self.riverine_extreme_ratio})"
            )
        return self

    @model_validator(mode="after")
    def _hazard_rp_thresholds_ordered(self) -> CRMAModelConfig:
        for rp, (medium, high) in self.hazard_thresholds_by_rp.items():
            if not (0.0 < medium < high <= 1.0):
                raise ValueError(
                    f"hazard_thresholds_by_rp[{rp}]=({medium}, {high}) must satisfy "
                    f"0 < medium < high <= 1"
                )
        return self

    # Data_Confidence dampening applied to the compound risk score, indexed by
    # confidence state [Low, Medium, High]. Precip ensembles are typically
    # Medium (IQR/median ~0.3-1.0), so Medium must stay near-neutral - otherwise
    # it structurally vetoes strong forecast signals (all-Green failure mode).
    confidence_damping: list[float] = Field(default_factory=lambda: [0.8, 0.95, 1.0])
    # False (default): confidence dampens only the obs branch. True: legacy whole-score.
    confidence_damps_forecast: bool = False
    # Soil-conditioned threshold: on saturated soil a flood needs less rain, so
    # surface the lower-return-period exceedance (lower effective rainfall bar).
    soil_conditioned_rp: bool = True
    saturated_rp: int = 2
    # Additive Compound_Risk score weights for the non-cluster evidence axes.
    weight_temporal_persist: float = 1.5
    weight_spatial_coverage: float = 1.0
    # Rainfall_Trend (Decreasing/Stable/Increasing) contributes a *centred*
    # (state-1)*weight term so Stable is exactly neutral (preserves the legacy
    # calibration): an intensifying 7-day trend nudges the score up by one
    # weight, a weakening trend down by one. The ±threshold (mm/day) bins the
    # IMERG 7-day slope into the three states (cf. mentor's ±2 mm/day).
    weight_rainfall_trend: float = 1.0
    rainfall_trend_threshold_mmday: float = 2.0

    # CPT parameters
    compound_score_thresholds: CompoundScoreThresholdsConfig = Field(
        default_factory=CompoundScoreThresholdsConfig
    )
    compound_cpt_buckets: CompoundCPTBucketsConfig = Field(default_factory=CompoundCPTBucketsConfig)
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

    @model_validator(mode="after")
    def _api_transition_stochastic(self) -> CRMAModelConfig:
        matrix = self.api_transition
        n = len(matrix)
        for col_idx in range(n):
            col_sum = sum(matrix[row_idx][col_idx] for row_idx in range(n))
            if abs(col_sum - 1.0) > 1e-6:
                raise ValueError(
                    f"api_transition column {col_idx} sums to {col_sum:.6f}, "
                    f"expected 1.0 (pgmpy requires column-stochastic matrices)"
                )
        return self


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
    # "max" / high percentile ("pNN") for the hazard so a localized flood peak
    # is not diluted over the admin-1 polygon (see risk_engine._process_day).
    method: str = "max"
    min_coverage_fraction: float = 0.5


class Component3OutputConfig(BaseModel):
    geojson: bool = True
    zarr: bool = True
    eahw_export: bool = True


class RiverineFeedConfig(BaseModel):
    """Populates riverine_ratio by pooling upstream forecast intensity.

    Data-side counterpart of crma_model.riverine_aware_hazard. When enabled, the
    risk engine pools each unit's upstream tail ratios (curated topology) into a
    riverine_ratio, feeding catchment-routed floods into Forecast_Hazard.
    """

    enabled: bool = True
    upstream_map_path: str = "data/river_basins/upstream_admin1.yaml"
    attenuation: float = Field(default=0.9, gt=0.0, le=1.0)
    aggregate: str = "max"  # "max" | "mean" over upstream units

    @field_validator("aggregate")
    @classmethod
    def _agg_valid(cls, v: str) -> str:
        if v not in ("max", "mean"):
            raise ValueError(f"aggregate must be 'max' or 'mean', got {v!r}")
        return v


class Component3Config(BaseModel):
    crma: CRMAConfig = Field(default_factory=CRMAConfig)
    api: APIConfig = Field(default_factory=APIConfig)
    crma_model: CRMAModelConfig = Field(default_factory=CRMAModelConfig)
    emdat_refinement: EMDATRefinementConfig = Field(default_factory=EMDATRefinementConfig)
    aggregation: AggregationConfig = Field(default_factory=AggregationConfig)
    riverine: RiverineFeedConfig = Field(default_factory=RiverineFeedConfig)
    output: Component3OutputConfig = Field(default_factory=Component3OutputConfig)


# Dashboard


class TiTilerConfig(BaseModel):
    endpoint: str = ""
    cog_bucket: str = ""


class CalendarMapConfig(BaseModel):
    signal_threshold_yellow: float = 0.15
    signal_threshold_orange: float = 0.30
    signal_threshold_red: float = 0.50

    @model_validator(mode="after")
    def _thresholds_ordered(self) -> CalendarMapConfig:
        y, o, r = (
            self.signal_threshold_yellow,
            self.signal_threshold_orange,
            self.signal_threshold_red,
        )
        if not (y < o < r):
            raise ValueError(
                f"Calendar map thresholds must satisfy yellow < orange < red, got {y} < {o} < {r}"
            )
        return self


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
    aifs_track: AIFSTrackConfig = Field(default_factory=AIFSTrackConfig)
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

    base_cfg = OmegaConf.load(DEFAULT_CONFIG_PATH)

    if path is not None and path.exists():
        override = OmegaConf.load(path)
        merged = OmegaConf.merge(base_cfg, override)
    else:
        merged = base_cfg

    return GIKConfig.model_validate(OmegaConf.to_container(merged, resolve=True))
