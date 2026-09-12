"""
evaluate.py — Compare an expert answer with the algorithm (pure Python, no numpy).

The expert is the reference. Outcomes:

    agree_hit          expert and algorithm both see DANA(s), one-to-one match
    partial_match      some systems match, but there are extra expert or algorithm systems
    location_mismatch  both see a DANA but no expert click falls inside an algorithm object
    algo_miss          expert sees a DANA, the algorithm has none
    algo_false_alarm   expert sees no DANA, the algorithm has one
    agree_null         neither sees a DANA
"""

import base64
import functools
import json
import math
import pathlib
import zlib

AGREE = ("agree_hit", "agree_null")
OUTCOMES = (
    "agree_hit",
    "partial_match",
    "location_mismatch",
    "algo_miss",
    "algo_false_alarm",
    "agree_null",
)
NEAR_CYCLONE_KM = 300.0


# ---------------------------------------------------------------------------
# Masks and geometry
# ---------------------------------------------------------------------------
def decode_mask(b64):
    """Return the packed bitmap (bytes) written by build_cases.encode_mask."""
    return zlib.decompress(base64.b64decode(b64))


def mask_bit(bits, k):
    return (bits[k >> 3] >> (7 - (k & 7))) & 1


def mask_indices(bits, n):
    out = []
    for byte_i, byte in enumerate(bits):
        if not byte:
            continue
        for b in range(8):
            if byte & (0x80 >> b):
                k = byte_i * 8 + b
                if k < n:
                    out.append(k)
    return out


def grid_index(grid, lat, lon):
    i = round((lat - grid["lat0"]) / grid["dlat"])
    j = round((lon - grid["lon0"]) / grid["dlon"])
    if 0 <= i < grid["nlat"] and 0 <= j < grid["nlon"]:
        return i, j
    return None


def cell_latlon(grid, k):
    i, j = divmod(k, grid["nlon"])
    return grid["lat0"] + i * grid["dlat"], grid["lon0"] + j * grid["dlon"]


def haversine_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(min(1.0, math.sqrt(a)))


def bearing_deg(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


def in_box(box, lat, lon):
    return box[0] <= lon <= box[1] and box[2] <= lat <= box[3]


# ---------------------------------------------------------------------------
# Case data
# ---------------------------------------------------------------------------
class CaseData:
    """Decoded private evaluation data of one case (cases/eval/<case_id>.json)."""

    def __init__(self, d):
        self.raw = d
        self.case_id = d["case_id"]
        self.grid = d["grid"]
        self.box = d["box"]
        self.n = self.grid["nlat"] * self.grid["nlon"]
        self.cols = d["cols"]
        self.cyclones = d["cyclones"]
        self._bits = {}
        self._cells = {}

    def bits(self, kind, obj):
        key = (kind, obj["id"])
        if key not in self._bits:
            self._bits[key] = decode_mask(obj["mask"])
        return self._bits[key]

    def cells(self, kind, obj):
        key = (kind, obj["id"])
        if key not in self._cells:
            self._cells[key] = mask_indices(self.bits(kind, obj), self.n)
        return self._cells[key]

    def contains(self, kind, obj, k):
        return bool(mask_bit(self.bits(kind, obj), k))

    def nearest_cell_km(self, kind, obj, lat, lon):
        best = float("inf")
        for k in self.cells(kind, obj):
            clat, clon = cell_latlon(self.grid, k)
            # cheap reject before the trig
            if abs(clat - lat) * 111.0 > best:
                continue
            best = min(best, haversine_km(lat, lon, clat, clon))
        return best


@functools.lru_cache(maxsize=256)
def _load_case(path):
    return CaseData(json.loads(pathlib.Path(path).read_text()))


def load_case(eval_dir, case_id):
    return _load_case(str(pathlib.Path(eval_dir) / f"{case_id}.json"))


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------
def _slim_record(rec):
    """Short criteria summary (full record is kept alongside)."""
    if not rec:
        return None
    return {
        "first_failed_criterion": rec.get("first_failed_criterion"),
        "failed_criteria": rec.get("failed_criteria"),
        "margins": {
            k: v.get("margin") for k, v in rec.get("criteria", {}).items() if "margin" in v
        },
    }


def evaluate_answer(case, has_dana, clicks):
    """Evaluate one answer.

    clicks : list of {"lat": float, "lon": float} (ignored when has_dana is False)
    Returns dict with outcome, counts, click_details, algo_details.
    """
    clicks = clicks if has_dana else []
    grid = case.grid
    cy_by_id = {c["id"]: c for c in case.cyclones}

    click_details = []
    for idx, c in enumerate(clicks):
        lat, lon = float(c["lat"]), float(c["lon"])
        ij = grid_index(grid, lat, lon)
        k = ij[0] * grid["nlon"] + ij[1] if ij else None
        det = {
            "idx": idx,
            "lat": round(lat, 3),
            "lon": round(lon, 3),
            "outside_box": not in_box(case.box, lat, lon),
            "outside_grid": ij is None,
            "inside_col_id": None,
            "inside_cy_id": None,
            "matched_col_id": None,
            "duplicate_of_col": None,
            "cols": [],
            "nearest_cyclone": None,
        }
        if k is not None:
            for col in case.cols:
                if det["inside_col_id"] is None and case.contains("col", col, k):
                    det["inside_col_id"] = col["id"]
            for cy in case.cyclones:
                if det["inside_cy_id"] is None and case.contains("cy", cy, k):
                    det["inside_cy_id"] = cy["id"]
        for col in case.cols:
            det["cols"].append(
                {
                    "id": col["id"],
                    "dist_mask_km": round(case.nearest_cell_km("col", col, lat, lon), 1),
                    "dist_zmin_km": round(
                        haversine_km(lat, lon, col["zmin_lat"], col["zmin_lon"]), 1
                    ),
                    "dist_centroid_km": round(
                        haversine_km(lat, lon, col["centroid_lat"], col["centroid_lon"]), 1
                    ),
                    "bearing_click_to_zmin_deg": round(
                        bearing_deg(lat, lon, col["zmin_lat"], col["zmin_lon"]), 0
                    ),
                    "same_parent_cyclone": det["inside_cy_id"] == col["id"],
                }
            )
        # nearest tracked cyclone (inside, or nearest mask cell within NEAR_CYCLONE_KM)
        best = None
        for cy in case.cyclones:
            dkm = (
                0.0 if det["inside_cy_id"] == cy["id"] else case.nearest_cell_km("cy", cy, lat, lon)
            )
            if dkm <= NEAR_CYCLONE_KM and (best is None or dkm < best[0]):
                best = (dkm, cy)
        if best is not None:
            dkm, cy = best
            det["nearest_cyclone"] = {
                "id": cy["id"],
                "dist_km": round(dkm, 1),
                "is_col": cy["is_col"],
                **_slim_record(cy.get("record")),
            }
        else:
            det["nearest_cyclone"] = {"no_cyclone_object": True}
        click_details.append(det)

    # greedy one-to-one matching: each algorithm object at most once
    matched_cols = {}
    for det in click_details:
        cid = det["inside_col_id"]
        if cid is None:
            continue
        if cid in matched_cols:
            det["duplicate_of_col"] = cid
        else:
            matched_cols[cid] = det["idx"]
            det["matched_col_id"] = cid

    n_systems = sum(1 for d in click_details if d["duplicate_of_col"] is None)
    n_matched = len(matched_cols)
    n_algo_missed = n_systems - n_matched  # expert systems not found by the algorithm
    n_algo_extra = len(case.cols) - n_matched  # algorithm systems the expert did not mark
    algo_has = len(case.cols) > 0

    if not has_dana:
        outcome = "algo_false_alarm" if algo_has else "agree_null"
    elif not algo_has:
        outcome = "algo_miss"
    elif n_systems == 0:  # yes without clicks (UI prevents it): judge detection only
        outcome = "agree_hit"
    elif n_matched == 0:
        outcome = "location_mismatch"
    elif n_algo_missed == 0 and n_algo_extra == 0:
        outcome = "agree_hit"
    else:
        outcome = "partial_match"

    algo_cols = []
    for col in case.cols:
        algo_cols.append(
            {
                "id": col["id"],
                "matched_click": matched_cols.get(col["id"]),
                "zmin_lat": col["zmin_lat"],
                "zmin_lon": col["zmin_lon"],
                "centroid_lat": col["centroid_lat"],
                "centroid_lon": col["centroid_lon"],
                "area_km2": col.get("area_km2"),
                "life_hours": col.get("life_hours"),
                "hours_since_onset": col.get("hours_since_onset"),
                "hours_to_decay": col.get("hours_to_decay"),
                "record": cy_by_id.get(col["id"], {}).get("record"),
            }
        )
    near_ids = {
        d["nearest_cyclone"]["id"]
        for d in click_details
        if d["nearest_cyclone"] and "id" in d["nearest_cyclone"]
    }
    algo_details = {
        "algo_n_cols": len(case.cols),
        "cols": algo_cols,
        "cyclones_near_clicks": [
            {
                "id": cy["id"],
                "is_col": cy["is_col"],
                "zmin_lat": cy["zmin_lat"],
                "zmin_lon": cy["zmin_lon"],
                "col_life": cy.get("col_life"),
                "record": cy.get("record"),
            }
            for cy in case.cyclones
            if cy["id"] in near_ids
        ],
    }
    return {
        "outcome": outcome,
        "n_systems": n_systems,
        "n_matched": n_matched,
        "n_algo_missed": n_algo_missed,
        "n_algo_extra": n_algo_extra,
        "click_details": click_details,
        "algo_details": algo_details,
    }


def is_disagreement(outcome):
    return outcome not in AGREE
