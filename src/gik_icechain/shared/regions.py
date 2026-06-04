"""East Africa spatial definitions — bounding boxes, country codes, admin loaders."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import geopandas as gpd


EAST_AFRICA_BBOX: dict[str, tuple[float, float]] = {
    "lat": (-12.0, 23.0),
    "lon": (22.0, 52.0),
}

EAST_AFRICA_COUNTRIES_ISO3: frozenset[str] = frozenset(
    {
        "BDI",  # Burundi
        "DJI",  # Djibouti
        "ERI",  # Eritrea
        "ETH",  # Ethiopia
        "KEN",  # Kenya
        "MDG",  # Madagascar
        "RWA",  # Rwanda
        "SOM",  # Somalia
        "SSD",  # South Sudan
        "TZA",  # Tanzania
        "UGA",  # Uganda
    }
)

# Mapping ISO3 country code → CRMA climate cluster (plain strings, no circular import).
# Source: ICPAC E4DRR regional classification (2024).
COUNTRY_CLUSTER: dict[str, str] = {
    "BDI": "equatorial_east",
    "DJI": "horn_arid",
    "ERI": "horn_arid",
    "ETH": "great_rift",
    "KEN": "equatorial_east",
    "MDG": "equatorial_east",
    "RWA": "equatorial_east",
    "SOM": "horn_arid",
    "SSD": "nile_basin",
    "TZA": "equatorial_east",
    "UGA": "equatorial_east",
}


def get_ea_slice() -> dict[str, slice]:
    """Return a lat/lon slice dict suitable for xarray ``.sel()``."""
    lat_min, lat_max = EAST_AFRICA_BBOX["lat"]
    lon_min, lon_max = EAST_AFRICA_BBOX["lon"]
    return {
        "latitude": slice(lat_max, lat_min),  # ECMWF data is N→S
        "longitude": slice(lon_min, lon_max),
    }


def load_admin1_geodataframe(path: Path) -> gpd.GeoDataFrame:
    """Load East Africa admin-1 boundaries from *path*.

    Expects columns: ``admin1_pcode``, ``admin1_name``, ``adm0_name``, ``geometry``.

    Args:
        path: Path to a GeoPackage or Shapefile.

    Returns:
        GeoDataFrame projected to EPSG:4326.
    """
    import geopandas as gpd

    gdf = gpd.read_file(path)
    if gdf.crs is None or gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs(epsg=4326)
    return gdf
