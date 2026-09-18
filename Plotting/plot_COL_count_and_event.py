#!/usr/bin/env python
"""
plot_COL_count_and_event.py — Annual COL count next to one detected event.

Two panels:

  left  — total number of Cut-off Lows per year (the Annual_count_COL figure:
          annual counts, 10- and 30-year rolling means, linear trend with its
          95% confidence interval), with the year of the event marked
  right  — map of one time step: Z500 shading, T850 contours, 850 hPa wind
          barbs and the region the algorithm detected as a COL hatched in red,
          the same view used by the expert evaluation website

The event date is an argument; without one the Valencia DANA of October 2024
is used. Fields come from the COL tracking output (`col_z500_{year}.nc`),
which already holds z500, t850, u850, v850 and the object labels, so only
that file and the per-event statistics CSVs are needed.

Usage examples:
  python Plotting/plot_COL_count_and_event.py
  python Plotting/plot_COL_count_and_event.py 1987-11-03
  python Plotting/plot_COL_count_and_event.py "2019-09-11 18:00"
  python Plotting/plot_COL_count_and_event.py 2024-10-29 --input /path/col_z500_2024.nc
"""

import argparse
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cartopy.crs as ccrs  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import seaborn as sns  # noqa: E402
import xarray as xr  # noqa: E402
from matplotlib.colors import BoundaryNorm  # noqa: E402
from matplotlib.gridspec import GridSpec  # noqa: E402
from plot_COL_stats import add_trend_with_ci  # noqa: E402

import atmotrack_config as cfg  # noqa: E402

# Valencia DANA — the case shown in DANASv3.pdf
DEFAULT_DATE = "2024-10-29 12:00"

Z500_LEVELS = np.arange(500, 600, 5)  # dam
T850_LEVELS = np.arange(250, 300, 5)  # K
BARB_SKIP = 10  # plot one wind barb every N grid points


def parse_date(text):
    """Accept 'YYYY-MM-DD' (noon assumed) or 'YYYY-MM-DD HH:MM'."""
    stamp = pd.to_datetime(text)
    if stamp == stamp.normalize():  # midnight means no time was given
        stamp += pd.Timedelta(hours=12)
    return stamp


def annual_counts(stats_dir):
    """Number of COLs per year, from the per-event statistics CSVs."""
    files = sorted(glob.glob(os.path.join(stats_dir, "events_stats_????.csv")))
    if not files:
        raise SystemExit(
            f"No events_stats_????.csv files in {os.path.abspath(stats_dir)} — "
            "run calc_COL_stats_all_watersheds.py first."
        )
    data = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
    return data.groupby(data["year"].astype(int)).size()


def load_event(track_file, date):
    """Return the time step of the tracking file closest to ``date``."""
    ds = xr.open_dataset(track_file)
    step = ds.sel(time=date, method="nearest")
    actual = pd.to_datetime(str(step.time.values))
    offset = abs(actual - date)
    if offset > pd.Timedelta(hours=12):
        raise SystemExit(
            f"Nearest time step in {os.path.basename(track_file)} is {actual}, "
            f"{offset} away from {date} — is the date inside the file?"
        )
    return ds, step, actual


def plot_annual_count(ax, counts, event_year):
    """The Annual_count_COL panel, with the event year marked."""
    years = counts.index.values
    ax.step(years, counts.values, where="mid", color="black", label="Cut-off Lows")
    ax.plot(
        years,
        counts.rolling(window=10, min_periods=10, center=True).mean(),
        color="red",
        label="10-year Rolling Mean",
    )
    ax.plot(
        years,
        counts.rolling(window=30, min_periods=30, center=True).mean(),
        color="blue",
        label="30-year Rolling Mean",
    )
    add_trend_with_ci(ax, years, counts.values, scale_factor=10)

    if years.min() <= event_year <= years.max():
        ax.axvline(event_year, color="0.4", lw=1, ls="--", zorder=0)
        ax.annotate(
            str(event_year),
            xy=(event_year, ax.get_ylim()[1]),
            xytext=(3, -12),
            textcoords="offset points",
            color="0.4",
            fontsize=9,
        )

    ax.set_title("Total Number of Cut-off Lows per Year")
    ax.set_ylabel("Number of Cut-off Lows")
    ax.set_xlabel("Year")
    ax.legend(loc="upper left", fontsize=9)
    ax.grid()


def plot_event_map(ax, step, lon, lat, date):
    """Z500 shading, T850 contours, wind barbs and the detected COL region."""
    lon2d, lat2d = np.meshgrid(lon, lat)
    cmap = sns.color_palette("Spectral_r", as_cmap=True)
    norm = BoundaryNorm(Z500_LEVELS, ncolors=cmap.N, extend="both")

    contourf = ax.contourf(
        lon2d,
        lat2d,
        step.z500 / 100.0,
        cmap=cmap,
        norm=norm,
        levels=Z500_LEVELS,
        extend="both",
        transform=ccrs.PlateCarree(),
        zorder=101,
    )

    line_contour = ax.contour(
        lon2d,
        lat2d,
        step.t850,
        levels=T850_LEVELS,
        colors="b",
        linewidths=1.5,
        transform=ccrs.PlateCarree(),
        zorder=103,
    )
    ax.clabel(line_contour, colors=["b"], inline=True, fmt=" {:.0f} ".format, zorder=103)

    ax.barbs(
        lon2d[::BARB_SKIP, ::BARB_SKIP],
        lat2d[::BARB_SKIP, ::BARB_SKIP],
        step.u850.values[::BARB_SKIP, ::BARB_SKIP],
        step.v850.values[::BARB_SKIP, ::BARB_SKIP],
        length=5,
        transform=ccrs.PlateCarree(),
        zorder=104,
    )

    # The region the algorithm labelled as a COL at this time step.
    obj_mask = (step.col_objects > 0).astype(int)
    detected = int(obj_mask.sum()) > 0
    if detected:
        hatched = ax.contourf(
            lon2d,
            lat2d,
            obj_mask,
            levels=[0.5, 1.5],
            colors="none",
            hatches=["///"],
            transform=ccrs.PlateCarree(),
            zorder=105,
        )
        hatched.set_edgecolor("red")

    ax.coastlines(linewidth=0.5, zorder=102, resolution="50m")
    gl = ax.gridlines(
        crs=ccrs.PlateCarree(),
        xlocs=range(-180, 181, 10),
        ylocs=range(-80, 81, 10),
        draw_labels=True,
        zorder=102,
        linewidth=0.2,
        color="k",
        linestyle="--",
    )
    gl.top_labels = gl.right_labels = False

    title = f"Z500 and T850 {date.strftime('%Y-%m-%d %H:%M')} UTC"
    if not detected:
        title += " — no COL detected"
    ax.set_title(title)
    return contourf


def parse_args():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "date",
        nargs="?",
        default=DEFAULT_DATE,
        help=f"Event date, YYYY-MM-DD or 'YYYY-MM-DD HH:MM' (default: {DEFAULT_DATE})",
    )
    parser.add_argument(
        "--input",
        default=None,
        help="COL tracking file (default: data_tracking/col_z500_{year}.nc)",
    )
    parser.add_argument(
        "--stats-dir",
        default=cfg.stats_dir,
        help="Directory with events_stats_????.csv (default: stats_dir from config.toml)",
    )
    parser.add_argument(
        "--outdir",
        default=cfg.plots_dir,
        help="Output directory (default: plots_dir from config.toml)",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    date = parse_date(args.date)

    track_file = args.input or os.path.join(cfg.data_tracking, f"col_z500_{date.year:04d}.nc")
    if not os.path.exists(track_file):
        raise SystemExit(
            f"{track_file} not found — run COL_tracking_ERA5.py for {date.year} first."
        )

    counts = annual_counts(args.stats_dir)
    ds, step, actual = load_event(track_file, date)
    lat = ds.latitude.squeeze().values
    lon = ds.longitude.squeeze().values

    fig = plt.figure(figsize=(17, 6.5), constrained_layout=True)
    gs = GridSpec(1, 2, figure=fig, width_ratios=[1.3, 1])

    plot_annual_count(fig.add_subplot(gs[0]), counts, actual.year)

    ax_map = fig.add_subplot(gs[1], projection=ccrs.PlateCarree())
    contourf = plot_event_map(ax_map, step, lon, lat, actual)
    cbar = fig.colorbar(contourf, ax=ax_map, shrink=0.85)
    cbar.set_label("Z500 (dam)")

    os.makedirs(args.outdir, exist_ok=True)
    out_png = os.path.join(args.outdir, f"COL_count_and_event_{actual.strftime('%Y%m%d%H')}.png")
    fig.savefig(out_png, dpi=150)
    print(f"Saved {out_png}")


if __name__ == "__main__":
    main()
