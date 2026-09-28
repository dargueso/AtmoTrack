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
from matplotlib.patches import Patch, Rectangle  # noqa: E402
from plot_COL_stats import add_trend_with_ci  # noqa: E402

import atmotrack_config as cfg  # noqa: E402

# Valencia DANA — the case shown in DANASv3.pdf
DEFAULT_DATE = "2024-10-29 12:00"

Z500_LEVELS = np.arange(500, 600, 5)  # dam
T850_LEVELS = np.arange(250, 300, 5)  # K
BARB_SKIP = 14  # plot one wind barb every N grid points
HATCH_LW = 1.2  # stroke width of the hatching over the detected COL
BARB_LENGTH = 4.5  # barb length in points, on the map and in the key
BARB_KEY_SPEEDS = (5, 10, 25, 50)  # m/s: half barb, full barb, two and a half, pennant
# The expert evaluation site draws the study region at lw 2.2 on a 6.9 in wide
# map; this is the same weight scaled to the narrower map used here.
STUDY_REGION_LW = 1.6


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

    ax.set_title("a", loc="left", fontweight="bold", fontsize=13)
    ax.set_title("Total Number of Cut-off Lows per Year", fontsize=12)
    ax.set_ylabel("Number of Cut-off Lows", fontsize=10)
    ax.set_xlabel("Year", fontsize=10)
    ax.tick_params(labelsize=9)
    ax.legend(loc="upper left", fontsize=9)
    ax.grid()


def add_barb_key(ax, y0):
    """A small box of sample barbs, so the barb glyphs can be read as speeds."""
    key_ax = ax.inset_axes([0.02, y0, 0.40, 0.13], zorder=106)
    key_ax.set_facecolor("white")
    key_ax.set_xticks([])
    key_ax.set_yticks([])
    for spine in key_ax.spines.values():
        spine.set_edgecolor("0.3")

    speeds = np.array(BARB_KEY_SPEEDS)
    x = np.arange(len(speeds)) + 0.5
    key_ax.set_xlim(0, len(speeds))
    key_ax.set_ylim(0, 1)
    key_ax.text(0.12, 0.82, "850 hPa wind (m s$^{-1}$)", ha="left", va="center", fontsize=7.5)
    key_ax.barbs(
        x,
        np.full(len(speeds), 0.46),
        speeds,
        np.zeros(len(speeds)),
        length=BARB_LENGTH,
        linewidth=0.5,
        color="0.2",
        pivot="middle",  # centre each glyph over its label
    )
    for xi, speed in zip(x, speeds):
        key_ax.text(xi, 0.15, str(speed), ha="center", va="center", fontsize=7)
    return key_ax


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
        linewidths=0.7,
        transform=ccrs.PlateCarree(),
        zorder=103,
    )
    ax.clabel(
        line_contour,
        colors=["b"],
        inline=True,
        fmt=" {:.0f} ".format,
        fontsize=7,
        zorder=103,
    )

    ax.barbs(
        lon2d[::BARB_SKIP, ::BARB_SKIP],
        lat2d[::BARB_SKIP, ::BARB_SKIP],
        step.u850.values[::BARB_SKIP, ::BARB_SKIP],
        step.v850.values[::BARB_SKIP, ::BARB_SKIP],
        length=BARB_LENGTH,
        linewidth=0.5,
        color="0.2",
        transform=ccrs.PlateCarree(),
        zorder=104,
    )

    # cfg.col_region is [lon_min, lon_max, lat_min, lat_max]: a COL track must
    # pass through this box, so the evaluation site outlines it on every frame.
    box = cfg.col_region
    region = Rectangle(
        (box[0], box[2]),
        box[1] - box[0],
        box[3] - box[2],
        fill=False,
        edgecolor="k",
        linestyle="--",
        linewidth=STUDY_REGION_LW,
        transform=ccrs.PlateCarree(),
        zorder=106,
        label="Study region",
    )
    ax.add_patch(region)

    # The region the algorithm labelled as a COL at this time step.
    obj_mask = (step.col_objects > 0).astype(int)
    detected = int(obj_mask.sum()) > 0
    handles = [region]
    if detected:
        # The key is built in the same rc_context so its hatching matches the map.
        with plt.rc_context({"hatch.linewidth": HATCH_LW}):
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
            key = Patch(
                facecolor="none",
                edgecolor="red",
                hatch="///",
                label="Detected COL region",
            )
        hatched.set_edgecolor("red")
        handles.insert(0, key)
    else:
        ax.text(
            0.034,  # offsets the bbox pad, so the box lines up with the keys
            0.245,
            "No COL detected",
            transform=ax.transAxes,
            fontsize=9,
            va="bottom",
            ha="left",
            zorder=106,
            bbox={
                "facecolor": "white",
                "alpha": 1.0,
                "edgecolor": "0.3",
                "boxstyle": "round,pad=0.5",
            },
        )

    legend = ax.legend(
        handles=handles,
        loc="lower left",
        bbox_to_anchor=(0.02, 0.17),
        bbox_transform=ax.transAxes,
        borderaxespad=0,
        fontsize=9,
        framealpha=1.0,
        facecolor="white",
        edgecolor="0.3",
        borderpad=0.6,
        handlelength=2.0,
        handleheight=1.4,
    )
    legend.set_zorder(106)

    add_barb_key(ax, y0=0.03)

    ax.coastlines(linewidth=0.9, zorder=102, resolution="50m")
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
    gl.xlabel_style = gl.ylabel_style = {"size": 8}

    ax.set_title("b", loc="left", fontweight="bold", fontsize=13)
    ax.set_title(
        f"Z500 and T850 {date.strftime('%Y-%m-%d %H:%M')} UTC",
        fontsize=12,
    )
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

    fig = plt.figure(figsize=(13.5, 6.5), layout="constrained")
    fig.get_layout_engine().set(w_pad=0.02, wspace=0.01)
    gs = GridSpec(1, 2, figure=fig, width_ratios=[1.5, 1])

    plot_annual_count(fig.add_subplot(gs[0]), counts, actual.year)

    ax_map = fig.add_subplot(gs[1], projection=ccrs.PlateCarree())
    contourf = plot_event_map(ax_map, step, lon, lat, actual)
    cbar = fig.colorbar(contourf, ax=ax_map, shrink=0.9, pad=0.02, aspect=30)
    cbar.set_label("Z500 (dam)", fontsize=10)
    cbar.ax.tick_params(labelsize=8)

    os.makedirs(args.outdir, exist_ok=True)
    out_png = os.path.join(args.outdir, f"COL_count_and_event_{actual.strftime('%Y%m%d%H')}.png")
    fig.savefig(out_png, dpi=150)
    print(f"Saved {out_png}")


if __name__ == "__main__":
    main()
