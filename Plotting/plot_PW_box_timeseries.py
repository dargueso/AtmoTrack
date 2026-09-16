#!/usr/bin/env python
"""
plot_PW_box_timeseries.py — Monthly-mean precipitable water over a box.

Reads the ERA5 monthly total column water vapour files written by
download_ERA5_PW.py (era5_monthly_TCWV_YYYY.nc), averages PW over a
lat/lon box (cos-latitude weighted) and produces a two-panel figure:

  left  — map of the long-term mean PW over the downloaded domain with the box
  right — PW time series over the box, its running mean and the linear trend

With all months (the default) the series is the monthly means, smoothed with a
12-month running mean, and the trend is fitted on deseasonalised anomalies.
With a season selected (DJF, MAM, JJA, SON or any list of months) the series is
one seasonal mean per year, smoothed with a 10-year running mean; seasons with a
missing month are dropped, so an incomplete final year never enters the trend.

The box-averaged series is also saved as CSV next to the figure.

Select the box and the season by editing the USER SETTINGS block below, or
override them on the command line:

  python Plotting/plot_PW_box_timeseries.py
  python Plotting/plot_PW_box_timeseries.py --box 38 41 -1 4.5 --name Valencia
  python Plotting/plot_PW_box_timeseries.py --season JJA
  python Plotting/plot_PW_box_timeseries.py --season 9,10,11 --anomalies --year-start 1979
"""

import argparse
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cartopy.crs as ccrs  # noqa: E402
import cartopy.feature as cfeature  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import xarray as xr  # noqa: E402
from matplotlib.gridspec import GridSpec  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

import atmotrack_config as cfg  # noqa: E402

# =============================================================================
# USER SETTINGS — edit the box here
# =============================================================================
BOX = dict(
    lat_min=35.0,  # °N
    lat_max=44.5,  # °N
    lon_min=-5.66,  # °E (negative = west)
    lon_max=10,  # °E
)
BOX_NAME = "Iberia"  # used in the title and output file names

SEASON = None  # None/"ALL" = every month; "DJF", "MAM", "JJA", "SON"; or e.g. [9, 10, 11]

YEAR_START = None  # first year to use (None = first available)
YEAR_END = None  # last year to use (None = last available)
PLOT_ANOMALIES = False  # True: plot anomalies instead of absolute values
# =============================================================================

FILE_PATTERN = "era5_monthly_TCWV_*.nc"
VAR = "tcwv"

SEASONS = {
    "DJF": [12, 1, 2],
    "MAM": [3, 4, 5],
    "JJA": [6, 7, 8],
    "SON": [9, 10, 11],
}
MONTH_ABBR = ["", "J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D"]
RUNNING_YEARS = 10  # window of the running mean in seasonal mode [years]

COLOR_MONTHLY = "#9ecae1"
COLOR_RUNNING = "#08519c"
COLOR_TREND = "#252525"
COLOR_BOX = "#cb181d"


def parse_season(season):
    """Return (label, months). ``months`` is None when every month is used.

    Accepts a season name ("DJF", …), a list of month numbers, or a
    comma-separated string of month numbers ("9,10,11").
    """
    if season is None or (isinstance(season, str) and season.upper() in ("", "ALL")):
        return "all months", None

    if isinstance(season, str):
        key = season.upper()
        if key in SEASONS:
            return key, SEASONS[key]
        try:
            months = [int(m) for m in season.replace(" ", "").split(",")]
        except ValueError:
            raise SystemExit(
                f"Unknown season {season!r}: use ALL, {', '.join(SEASONS)} "
                "or a list of month numbers such as 9,10,11"
            ) from None
    else:
        months = [int(m) for m in season]

    if not months or any(m < 1 or m > 12 for m in months):
        raise SystemExit(f"Invalid month numbers in season {season!r}")
    return "".join(MONTH_ABBR[m] for m in months), months


def seasonal_mean(series, months):
    """Mean over ``months`` for each year, weighted by month length.

    Years missing any of the requested months are dropped. For seasons that
    straddle the turn of the year (e.g. DJF) December is assigned to the
    following year, the usual convention.
    """
    sel = series.sel(time=series.time.dt.month.isin(months))
    year = sel.time.dt.year
    if 12 in months and 1 in months:
        year = year + (sel.time.dt.month == 12)
    sel = sel.assign_coords(year=("time", year.values))

    weights = sel.time.dt.days_in_month.assign_coords(year=("time", year.values))
    grouped = (sel * weights).groupby("year").sum() / weights.groupby("year").sum()
    complete = sel.groupby("year").count() == len(months)
    out = grouped.where(complete, drop=True)

    if out.sizes["year"] == 0:
        raise SystemExit("No complete season in the selected years — widen the year range.")
    return out


def load_pw(datadir, year_start=None, year_end=None):
    """Open all annual TCWV files and return PW (mm) with dims (time, lat, lon)."""
    files = sorted(glob.glob(os.path.join(datadir, FILE_PATTERN)))
    if not files:
        raise SystemExit(
            f"No files matching {FILE_PATTERN} in {datadir}. Run download_ERA5_PW.py first."
        )
    ds = xr.open_mfdataset(files, combine="by_coords")

    # New CDS files use valid_time; older ones use time.
    if "valid_time" in ds.dims:
        ds = ds.rename(valid_time="time")
    # Older CDS files mixing ERA5 and ERA5T carry an expver dimension.
    if "expver" in ds.dims:
        ds = ds.max("expver", skipna=True)
    ds = ds.drop_vars(["number", "expver"], errors="ignore")

    pw = ds[VAR].rename(latitude="lat", longitude="lon")

    # Normalise longitudes to [-180, 180) and make both axes ascending.
    pw = pw.assign_coords(lon=((pw.lon + 180) % 360) - 180).sortby("lon").sortby("lat")

    pw = pw.sel(
        time=slice(
            None if year_start is None else f"{year_start}-01-01",
            None if year_end is None else f"{year_end}-12-31",
        )
    )
    if pw.sizes["time"] == 0:
        raise SystemExit("No data left after applying the year range.")
    return pw.load()


def box_mean(pw, box):
    """Cos(lat)-weighted mean of PW over the box. Raises if the box is outside the data."""
    lat, lon = pw.lat.values, pw.lon.values
    if (
        box["lat_min"] >= box["lat_max"]
        or box["lon_min"] >= box["lon_max"]
        or box["lat_min"] < lat.min()
        or box["lat_max"] > lat.max()
        or box["lon_min"] < lon.min()
        or box["lon_max"] > lon.max()
    ):
        raise SystemExit(
            f"Box {box} is invalid or outside the data domain "
            f"(lat {lat.min()}–{lat.max()}, lon {lon.min()}–{lon.max()})."
        )
    sub = pw.sel(
        lat=slice(box["lat_min"], box["lat_max"]), lon=slice(box["lon_min"], box["lon_max"])
    )
    if sub.sizes["lat"] == 0 or sub.sizes["lon"] == 0:
        raise SystemExit(f"Box {box} contains no grid points; make it larger.")
    weights = np.cos(np.deg2rad(sub.lat))
    return sub.weighted(weights).mean(("lat", "lon"))


def linear_trend(time_in_years, values):
    """OLS fit against time in years. Returns (slope per year, fitted values)."""
    valid = np.isfinite(values)
    slope, intercept = np.polyfit(time_in_years[valid], values[valid], 1)
    return slope, slope * time_in_years + intercept


def build_series(series, months, anomalies):
    """Turn the box mean into what is plotted: x, y, its running mean and labels."""
    if months is None:
        # Monthly means; the trend is fitted on deseasonalised anomalies so that
        # an incomplete final year cannot bias it.
        if series.sizes["time"] < 24:
            raise SystemExit(
                f"Only {series.sizes['time']} months available — download at least two "
                "years before fitting a trend."
            )
        anom = series.groupby("time.month") - series.groupby("time.month").mean()
        t = (series.time.dt.year + (series.time.dt.month - 0.5) / 12).values
        slope, trend_anom = linear_trend(t, anom.values)
        values = anom if anomalies else series
        # Draw the anomaly trend around the long-term mean so it overlays the series.
        trend = trend_anom if anomalies else trend_anom + float(series.mean())
        running = values.rolling(time=12, center=True).mean()
        x = series.time.values
        labels = ("Monthly mean", "12-month running mean")
    else:
        seasonal = seasonal_mean(series, months)
        if seasonal.sizes["year"] < 3:
            raise SystemExit(
                f"Only {seasonal.sizes['year']} complete season(s) available — download "
                "more years before fitting a trend."
            )
        x = seasonal.year.values
        slope, trend_abs = linear_trend(x.astype(float), seasonal.values)
        # Anomalies are relative to the mean of the season itself.
        mean = float(seasonal.mean())
        values = seasonal - mean if anomalies else seasonal
        trend = trend_abs - mean if anomalies else trend_abs
        window = min(RUNNING_YEARS, values.sizes["year"])
        running = values.rolling(year=window, center=True).mean()
        labels = ("Seasonal mean", f"{window}-year running mean")

    ylabel = "PW anomaly (mm)" if anomalies else "Precipitable water (mm)"
    return x, values, running, trend, slope, labels, ylabel


def make_figure(pw, series, box, box_name, anomalies, months, season_label):
    x, plotted, running, trend_line, slope, labels, ylabel = build_series(series, months, anomalies)
    map_field = pw if months is None else pw.sel(time=pw.time.dt.month.isin(months))
    # Period of what is actually plotted (a first incomplete season may be dropped).
    if months is None:
        period = f"{int(series.time.dt.year.min())}–{int(series.time.dt.year.max())}"
    else:
        period = f"{x[0]}–{x[-1]}"

    fig = plt.figure(figsize=(16, 5.5), constrained_layout=True)
    gs = GridSpec(1, 2, figure=fig, width_ratios=[1, 2.3])

    # --- Map -----------------------------------------------------------------
    ax_map = fig.add_subplot(gs[0], projection=ccrs.PlateCarree())
    mean_map = map_field.mean("time")
    mesh = ax_map.pcolormesh(
        pw.lon, pw.lat, mean_map, cmap="Blues", shading="nearest", transform=ccrs.PlateCarree()
    )
    ax_map.add_feature(cfeature.COASTLINE, linewidth=0.6)
    ax_map.add_feature(cfeature.BORDERS, linewidth=0.3, edgecolor="0.3")
    ax_map.add_patch(
        Rectangle(
            (box["lon_min"], box["lat_min"]),
            box["lon_max"] - box["lon_min"],
            box["lat_max"] - box["lat_min"],
            fill=False,
            edgecolor=COLOR_BOX,
            linewidth=2,
            transform=ccrs.PlateCarree(),
            zorder=10,
        )
    )
    ax_map.set_extent(
        [float(pw.lon.min()), float(pw.lon.max()), float(pw.lat.min()), float(pw.lat.max())],
        crs=ccrs.PlateCarree(),
    )
    gl = ax_map.gridlines(draw_labels=True, linewidth=0.3, color="0.6", linestyle="--")
    gl.top_labels = gl.right_labels = False
    cbar = fig.colorbar(mesh, ax=ax_map, orientation="horizontal", shrink=0.85, pad=0.02)
    cbar.set_label(f"Mean PW {season_label} {period} (mm)")
    ax_map.set_title(f"Selected region: {box_name}", loc="left")

    # --- Time series ---------------------------------------------------------
    ax_ts = fig.add_subplot(gs[1])
    seasonal = months is not None
    ax_ts.plot(
        x,
        plotted,
        color=COLOR_MONTHLY,
        lw=1.2 if seasonal else 0.8,
        marker="o" if seasonal else None,
        ms=3,
        label=labels[0],
    )
    ax_ts.plot(x, running, color=COLOR_RUNNING, lw=2, label=labels[1])
    ax_ts.plot(
        x,
        trend_line,
        color=COLOR_TREND,
        lw=1.5,
        ls="--",
        label=f"Linear trend: {slope * 10:+.2f} mm/decade",
    )
    if anomalies:
        ax_ts.axhline(0, color="0.5", lw=0.8)
    ax_ts.set_ylabel(ylabel)
    ax_ts.set_xlim(x[0], x[-1])
    ax_ts.grid(axis="y", color="0.9", lw=0.8)
    ax_ts.spines[["top", "right"]].set_visible(False)
    ax_ts.legend(loc="upper left", bbox_to_anchor=(0, -0.06), frameon=False, ncols=3)
    ax_ts.set_title(
        f"ERA5 precipitable water ({season_label}), {box_name} "
        f"({box['lat_min']}–{box['lat_max']}°N, {box['lon_min']}–{box['lon_max']}°E), {period}",
        loc="left",
    )
    return fig


def parse_args():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--box",
        type=float,
        nargs=4,
        metavar=("LAT_MIN", "LAT_MAX", "LON_MIN", "LON_MAX"),
        help="Box to average over (default: BOX in the script)",
    )
    parser.add_argument("--name", default=BOX_NAME, help="Region name (default: %(default)s)")
    parser.add_argument(
        "--season",
        default=SEASON,
        help=(
            "Season to average: ALL, "
            + ", ".join(SEASONS)
            + ", or a list of month numbers such as 9,10,11 (default: SEASON in the script)"
        ),
    )
    parser.add_argument("--year-start", type=int, default=YEAR_START)
    parser.add_argument("--year-end", type=int, default=YEAR_END)
    parser.add_argument(
        "--anomalies",
        action="store_true",
        default=PLOT_ANOMALIES,
        help="Plot anomalies instead of absolute values",
    )
    parser.add_argument(
        "--datadir",
        default=cfg.data_era5,
        help="Input directory (default: data_era5 from config.toml)",
    )
    parser.add_argument(
        "--outdir",
        default=cfg.plots_dir,
        help="Output directory (default: plots_dir from config.toml)",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    box = (
        BOX
        if args.box is None
        else dict(zip(("lat_min", "lat_max", "lon_min", "lon_max"), args.box))
    )

    season_label, months = parse_season(args.season)

    pw = load_pw(args.datadir, args.year_start, args.year_end)
    series = box_mean(pw, box)

    os.makedirs(args.outdir, exist_ok=True)
    period_tag = "monthly" if months is None else season_label
    tag = f"PW_{period_tag}_{args.name.replace(' ', '_')}"

    csv_series = series if months is None else seasonal_mean(series, months)
    csv_series.to_series().rename("pw_mm").to_csv(os.path.join(args.outdir, f"{tag}.csv"))

    fig = make_figure(pw, series, box, args.name, args.anomalies, months, season_label)
    suffix = "_anom" if args.anomalies else ""
    out_png = os.path.join(args.outdir, f"{tag}{suffix}.png")
    fig.savefig(out_png, dpi=200)
    print(f"Saved {out_png}")


if __name__ == "__main__":
    main()
