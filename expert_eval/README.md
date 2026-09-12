# DANA Expert Check

A small self-hosted website where experts judge ERA5 maps: *is there a DANA (cut-off low) centred in
the box, and where is its centre?* Their answers are compared with the AtmoTrack COL algorithm
(`tracking/col.py`). The **expert is the reference**. Every answer is stored with the algorithm's
criteria values so disagreements can be traced to specific criteria and the thresholds tuned.

```
build_cases.py      offline: scan tracking output, pick cases, render maps (needs the AtmoTrack env)
app.py              Flask server (needs only Flask + cases/)
evaluate.py         click ↔ algorithm matching and outcomes (stdlib)
sampler.py          stratified, disagreement-weighted session drawing (stdlib)
criteria.py         per-step re-implementation of the col.py criteria (numpy/scipy)
enrich_responses.py offline: criteria around every expert click
admin.py            invite codes, CSV exports, report, threshold sweep (stdlib)
settings.toml       session sizes, class mix, sampling priors, build options
```

## 1. Build the case pool

Run from `expert_eval/` with the AtmoTrack environment, **after** the COL tracking has produced
`data_tracking/col_z500_YYYY.nc`:

```bash
python build_cases.py --scan-only             # pool statistics only
python build_cases.py --n-cases 600 --jobs 8  # scan + select + render
```

- Files modified less than 10 minutes ago are skipped (tracking still writing); `--force` overrides.
- Each tracked z500 cyclone is re-evaluated against every COL criterion. The log line
  `Criteria re-evaluation vs saved col_objects: 0/N records differ` confirms the re-implementation
  reproduces the saved `col_objects`. If thresholds in `config.toml` changed since the tracking run,
  this number will not be 0.
- Re-running **appends** new cases (existing ids, frames and answers stay valid). Scans are cached in
  `cases/.scan/`.
- About 600 cases × 9 frames ≈ 450 MB of PNGs.

### Case classes

| class | meaning |
|---|---|
| `col_clear` | algorithm COL, mid-life, not short-lived, isolation well above threshold, well inside the box |
| `col_borderline` | algorithm COL tagged `short_lived`, `onset`, `decay`, `marginal_isolation`, `near_box_edge` |
| `nocol_clear` | no z500 cyclone with its minimum within the box ± 5° |
| `nocol_borderline` | a z500 cyclone nearby that is not a COL, tagged with the failing criterion (`fail:isolation`, `fail:eastward_flow`, `fail:region`, `fail:duration`, …), `pre_onset`/`post_decay`, `marginal_isolation_fail`, `near_box_outside` |

The pool quotas are in `settings.toml [build]`. Selection spreads cases across seasons and borderline
tags, and keeps cases from the same event at least 72 h apart.

## 2. Run the site

```bash
pip install -r requirements.txt           # flask
python admin.py add-codes --n 5 --label AEMET
python app.py                             # http://<host>:5050
```

Experts sign in with **either** an invite code **or** their details (name, email, experience).
For more than a handful of simultaneous users, serve with a WSGI server, e.g.
`pip install waitress && waitress-serve --port 5050 app:app`.

### What an expert sees

1. The instructions and a choice of session size (10 by default; 20 or 30 also available).
2. For each map, which shows only the month so experts can't recognise famous events:
   - Yes/No.
   - After *Yes*, a click on each centre; several are allowed and a marker can be clicked to remove it.
   - An *Unsure* tick box.
   - A ±24 h loop.
3. **End training** at any time. The summary is worded from the expert's side: "the algorithm
   detected X of your Y DANAs…". It includes a gallery of disagreements with the algorithm overlay
   and an optional *keep / would change + reasons + comment* review.
4. *Continue with 10 more* extends the same session.

Sessions contain 40–60 % algorithm-COL cases. Cases experts often disagree on (or mark unsure) are drawn
more often (`settings.toml [sampling]`). Cases with few answers get a novelty bonus.

## 3. Analyse

```bash
python enrich_responses.py                # criteria around each click (from stored fields)
python admin.py report                    # outcomes, disagreement by class/tag/season/expert,
                                          # failed criteria for misses, margins of false alarms,
                                          # location offsets, reviews, most disputed cases
python admin.py export responses.csv      # one row per answer
python admin.py export-tuning tuning.csv  # one row per (answer, cyclone object) with all criteria
                                          # values/margins + expert label; agreements included
python admin.py sweep                     # isolation threshold grid vs expert labels (CSI/POD/FAR)
```

Outcomes, with the expert as reference:

| outcome | expert | algorithm |
|---|---|---|
| `agree_hit` | DANA | COL, every centre inside a COL, one-to-one |
| `partial_match` | DANA | some centres match, extra expert or algorithm systems |
| `location_mismatch` | DANA | COL elsewhere (no click inside) |
| `algo_miss` | DANA | none |
| `algo_false_alarm` | none | COL |
| `agree_null` | none | none |

Stored per answer (`responses.click_details`, `responses.algo_details`):

- **every click:** the COL or cyclone it falls in, distances to each COL mask, z500 minimum and centroid, bearing, and whether it is outside the box
- **misses:** the nearest tracked cyclone within 300 km with its full criteria record (`first_failed_criterion`, values and margins), or `no_cyclone_object`
- **false alarms and hits:** the COL's criteria record, area, lifetime and position in its lifecycle
- **sessions:** the COL thresholds and git hash of the build (`sessions.algo_version`)

## Tests

```bash
EXPERT_EVAL_CASES_DIR=/path/to/small/pool python -m pytest -q test_expert_eval.py
```

`EXPERT_EVAL_CASES_DIR`, `EXPERT_EVAL_DB` and `EXPERT_EVAL_SETTINGS` override the paths in settings.toml.
