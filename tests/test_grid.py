"""Grid conventions: latitude order, longitude convention, periodicity, curvilinear grids."""

import numpy as np
import pytest

from atmotrack.grid import Grid, is_global_periodic, lon_convention, to_pm180
from tests.conftest import LAT2D, LON2D, TIMES, z500_field


def test_grid_describe_regular():
    g = Grid(LON2D, LAT2D)
    assert g.is_regular
    assert not g.lat_ascending
    assert g.lon_convention == "-180..180"
    assert not g.is_global_periodic
    assert 40 < g.spacing_km < 60


def test_lon_convention_and_wrap():
    assert lon_convention(np.array([0.0, 90.0, 270.0])) == "0..360"
    assert lon_convention(np.array([-170.0, 10.0])) == "-180..180"
    assert np.allclose(to_pm180(np.array([0.0, 190.0, 350.0])), [0.0, -170.0, -10.0])


@pytest.mark.parametrize("convention", ["-180..180", "0..360"])
def test_global_periodic_detection(convention):
    lon = np.arange(-180.0, 180.0, 2.5) if convention == "-180..180" else np.arange(0.0, 360.0, 2.5)
    lat = np.arange(60.0, -61.0, -2.5)
    lon2d, lat2d = np.meshgrid(lon, lat)
    assert is_global_periodic(lon2d, lat2d)
    # a limited-area subset is not periodic
    assert not is_global_periodic(lon2d[:, :50], lat2d[:, :50])
    # a seam-duplicated grid (… 357.5, 360) is not seamless-periodic
    lon_dup = np.append(np.arange(0.0, 360.0, 2.5), 360.0)
    lon2d_dup, lat2d_dup = np.meshgrid(lon_dup, lat)
    assert not is_global_periodic(lon2d_dup, lat2d_dup)


def _run_cy_and_col(lon2d, lat2d, z):
    from atmotrack.tracking import COL_tracking, CY_ACY_z500_tracking

    nt = z.shape[0]
    rng = np.random.default_rng(0)
    u200 = rng.normal(0, 20, z.shape)
    cy, _ = CY_ACY_z500_tracking(z, TIMES, lon2d, lat2d, nc_file=None)
    col = COL_tracking(
        cy,
        z,
        u200,
        rng.normal(0, 10, z.shape),
        rng.normal(0, 10, z.shape),
        np.full(z.shape, 270.0),
        np.zeros(z.shape),
        np.zeros(z.shape),
        times=TIMES,
        Lon=lon2d,
        Lat=lat2d,
        nc_file=None,
    )
    assert cy.shape == (nt,) + lon2d.shape
    return cy, col


def test_ascending_latitude_gives_same_objects():
    """Flipping the latitude order flips the objects, nothing else."""
    z = z500_field()
    cy_desc, col_desc = _run_cy_and_col(LON2D, LAT2D, z)
    cy_asc, col_asc = _run_cy_and_col(LON2D[::-1, :], LAT2D[::-1, :], z[:, ::-1, :])
    assert np.array_equal(cy_desc > 0, (cy_asc > 0)[:, ::-1, :])
    assert np.array_equal(col_desc > 0, (col_asc > 0)[:, ::-1, :])


def test_lon_0_360_gives_same_objects():
    """Longitudes in 0..360 (domain not crossing 0°) give the same objects."""
    lon = np.arange(300.0, 350.25, 0.5)  # == -60..-10 in -180..180
    lat = LAT2D[:, 0]
    lon2d, lat2d = np.meshgrid(lon, lat)
    z_ref = z500_field(to_pm180(lon2d), lat2d, lat_c=40.0)
    cy_a, col_a = _run_cy_and_col(lon2d, lat2d, z_ref)
    cy_b, col_b = _run_cy_and_col(to_pm180(lon2d), lat2d, z_ref)
    assert np.array_equal(cy_a > 0, cy_b > 0)
    assert np.array_equal(col_a > 0, col_b > 0)


def test_curvilinear_grid_runs(tmp_path):
    """A rotated (curvilinear) grid runs through the trackers and is written with 2-D coords."""
    import xarray as xr

    from atmotrack.tracking import CY_ACY_slp_tracking
    from tests.conftest import slp_field

    # rotate the regular grid by 15° around its centre -> rows are no longer parallels
    theta = np.deg2rad(15.0)
    x = LON2D - LON2D.mean()
    y = LAT2D - LAT2D.mean()
    lon2d = LON2D.mean() + x * np.cos(theta) - y * np.sin(theta)
    lat2d = LAT2D.mean() + x * np.sin(theta) + y * np.cos(theta)
    g = Grid(lon2d, lat2d)
    assert not g.is_regular

    slp = slp_field(lon2d, lat2d)
    out = tmp_path / "cy_slp.nc"
    cy, acy = CY_ACY_slp_tracking(slp, TIMES, lon2d, lat2d, nc_file=str(out))
    assert cy.max() > 0
    with xr.open_dataset(out) as ds:
        assert ds["cy_slp_objects"].dims == ("time", "y", "x")
        assert ds["lat"].dims == ("y", "x")
        assert "latitude" not in ds.coords
        np.testing.assert_allclose(ds["lon"].values, lon2d, atol=1e-4)
