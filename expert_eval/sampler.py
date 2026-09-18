"""
sampler.py — Draw a session of cases: stratified by class and adaptive to disagreement.

Each session has between min_pos_frac and max_pos_frac cases in which the
algorithm detected a COL. Within each class a share of borderline cases is
drawn. Inside a stratum, cases are sampled without replacement with weight

    w = (base_weight + d) * novelty
    d = (n_disagree + unsure_weight * n_unsure + alpha) / (n + alpha + beta)

so cases where experts and the algorithm usually disagree come up more often,
while cases with few answers get a novelty bonus to keep coverage of the pool.
Borderline cases sharing tags with an already drawn case are down-weighted to
rotate through the different borderline situations.

High-impact cases (tag "high_impact") are guaranteed a share of every session
(settings [session] high_impact_fraction), drawn first with weight 1 / (1 + answers)
so all events collect answers evenly; they count towards the COL / non-COL balance.
"""

import json
import math
import random


def case_weight(n, n_disagree, n_unsure, sp):
    d = (n_disagree + sp["unsure_weight"] * n_unsure + sp["prior_alpha"]) / (
        n + sp["prior_alpha"] + sp["prior_beta"]
    )
    novelty = sp["novelty_bonus"] if n < sp["novelty_min_answers"] else 1.0
    return (sp["base_weight"] + d) * novelty


def _weighted_draw(pool, k, rng, diversify=False):
    pool = list(pool)
    weights = [c["weight"] for c in pool]
    out = []
    while pool and len(out) < k:
        i = rng.choices(range(len(pool)), weights=weights, k=1)[0]
        c = pool.pop(i)
        weights.pop(i)
        out.append(c)
        if diversify:
            tags = {t for t in c["tags"] if t != "cyclone_not_col"}
            for j, o in enumerate(pool):
                if tags & set(o["tags"]):
                    weights[j] *= 0.5
    return out


def draw_session(case_rows, n_cases, exclude, settings, rng=None):
    """Return (ordered list of case_ids, n_pos_planned).

    case_rows : iterable of mappings with case_id, category, tags (list or JSON),
                algo_n_cols, n, n_disagree, n_unsure
    exclude   : case_ids this expert has already answered or has pending
    """
    rng = rng or random.Random()
    ss, sp = settings["session"], settings["sampling"]
    strata = {"col_clear": [], "col_borderline": [], "nocol_clear": [], "nocol_borderline": []}
    for r in case_rows:
        if r["case_id"] in exclude or r["category"] not in strata:
            continue
        tags = r["tags"] if isinstance(r["tags"], list) else json.loads(r["tags"])
        strata[r["category"]].append(
            {
                "case_id": r["case_id"],
                "category": r["category"],
                "tags": tags,
                "n": r["n"] or 0,
                "weight": case_weight(r["n"] or 0, r["n_disagree"] or 0, r["n_unsure"] or 0, sp),
            }
        )

    lo = math.ceil(ss["min_pos_frac"] * n_cases - 1e-9)
    hi = math.floor(ss["max_pos_frac"] * n_cases + 1e-9)
    n_pos = rng.randint(lo, max(lo, hi))

    # high-impact share first (fewest answers most likely), then remove them from the strata
    pool_hi = [{**c, "weight": 1.0 / (1 + c["n"])} for cs in strata.values() for c in cs
               if "high_impact" in c["tags"]]  # fmt: skip
    k_hi = min(round(n_cases * ss.get("high_impact_fraction", 0)), len(pool_hi))
    hi_cases = _weighted_draw(pool_hi, k_hi, rng)
    hi_ids = {c["case_id"] for c in hi_cases}
    for cat in strata:
        strata[cat] = [c for c in strata[cat] if c["case_id"] not in hi_ids]
    hi_pos = [c for c in hi_cases if c["category"].startswith("col_")]
    hi_neg = [c for c in hi_cases if c["category"].startswith("nocol_")]

    def draw_class(prefix, k, border_frac):
        k_border = round(k * border_frac)
        border = _weighted_draw(strata[f"{prefix}_borderline"], k_border, rng, diversify=True)
        clear = _weighted_draw(strata[f"{prefix}_clear"], k - len(border), rng)
        if len(border) + len(clear) < k:  # not enough clear cases: top up with borderline
            taken = {c["case_id"] for c in border}
            rest = [c for c in strata[f"{prefix}_borderline"] if c["case_id"] not in taken]
            border += _weighted_draw(rest, k - len(border) - len(clear), rng, diversify=True)
        return border + clear

    pos = hi_pos + draw_class("col", max(0, n_pos - len(hi_pos)), ss["pos_borderline_frac"])
    neg = hi_neg + draw_class(
        "nocol", max(0, n_cases - n_pos - len(hi_neg)), ss["neg_borderline_frac"]
    )
    # if one class ran out, fill from the other so the session still has n_cases
    short = n_cases - len(pos) - len(neg)
    if short > 0:
        taken = {c["case_id"] for c in pos + neg}
        prefix = "col" if len(pos) < n_pos else "nocol"
        other = "nocol" if prefix == "col" else "col"
        rest = [
            c
            for cat in (f"{other}_borderline", f"{other}_clear")
            for c in strata[cat]
            if c["case_id"] not in taken
        ]
        extra = _weighted_draw(rest, short, rng)
        (neg if other == "nocol" else pos).extend(extra)
    chosen = pos + neg
    rng.shuffle(chosen)
    return [c["case_id"] for c in chosen], len(pos)
