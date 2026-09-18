# DANA Expert Check

A small self-hosted website where experts judge ERA5 maps: *is there a DANA (cut-off low) centred in
the box, and where is its centre?* Their answers are compared with the AtmoTrack COL algorithm
(`tracking/col.py`). The **expert is the reference**. Every answer is stored with the algorithm's
criteria values so disagreements can be traced to specific criteria and the thresholds tuned.

Live site: **https://meteorologia.uib.es/dana/** (PHP version on the group web host, see section 2b).

```
build_cases.py      offline: scan tracking output, pick cases, render maps (needs the AtmoTrack env)
app.py              Flask server for local use (needs only Flask + cases/)
php/                PHP server for the web host (same API and logic as app.py/evaluate.py/sampler.py)
host.sh             deploy to the web host, run admin commands there, pull the answers back
evaluate.py         click ↔ algorithm matching and outcomes (stdlib)
sampler.py          stratified, disagreement-weighted session drawing (stdlib)
schema.sql          database schema shared by the Python and PHP servers
static/             the web page (index.html, app.js, style.css), shared by both servers
criteria.py         per-step re-implementation of the col.py criteria (numpy/scipy)
enrich_responses.py offline: criteria around every expert click
admin.py            CSV exports, report, threshold sweep, invite codes for a local site (stdlib)
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

### High-impact events

`high_impact_events.toml` lists the high-impact DANA events (daily precipitation > 400 mm or
catastrophic floods) from the slides in `DANASv3.pdf` (slides 11-26): 31 events, each with the ERA5
map times shown on the slides. They are included with:

```bash
python build_cases.py --high-impact high_impact_events.toml --target-total 1000 --jobs 16
```

- **One case per event.** The case time is chosen so that the expert's ±24 h loop covers as many
  of the slide maps as possible (then: a slide map, the middle of the covered maps, the later time).
  The log lists the chosen times; set `case_time` in the TOML to override one.
- **Tagged, not revealed.** These cases get the tag `high_impact` and a `high_impact_event` id in
  their evaluation file and in the manifest; experts are not told which cases they are. An event
  whose time is already in the pool is only tagged.
- **Forced, then topped up.** `--target-total N` adds stratified cases until the pool has N cases
  with the category proportions of `[build] quota_*`; the high-impact cases count towards their
  category. (`--n-cases` still adds a fixed number instead.)
- **Answered often.** `[session] high_impact_fraction` (default 0.1) reserves at least that share of
  every session for high-impact cases, picking those with the fewest answers first, so all events
  build up answers evenly. They still count towards the 40–60 % COL balance.
- **Reported separately.** `admin.py report` has a *High-impact events* section, and
  `admin.py high-impact --csv events.csv` exports the per-event verdicts to discuss with reviewers
  (section 4).
- **Deploy after rebuilding.** `./host.sh deploy` uploads the new cases and refreshes the tags on
  the host database.

## 2. Run the site locally

```bash
pip install -r requirements.txt           # flask
python admin.py add-codes --n 5 --label AEMET
python app.py                             # http://<host>:5050
```

Experts sign in with a personal **invite code**. Without one, they use **Request a code** on the sign-in page
(name, email, institution, experience). The site creates the code at once and emails the request to the
organisers, who reply with the code (see section 2b). The database keeps only a keyed fingerprint of the
address, so a repeat request brings back the same code.
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

## 2b. Live site on meteorologia.uib.es (PHP)

**https://meteorologia.uib.es/dana/**

The group web host is shared hosting: no Python 3 and no long-running processes, but PHP 8.1 and
SQLite. It runs the PHP version of the server in `php/`, which has the same requests, evaluation and
sampling as `app.py`, `evaluate.py` and `sampler.py`. The web page (`static/`), the case pool and the
database format are shared with the Flask version, so the Python analysis tools work on its answers.

### What lives where

On the host, under `/srv/www/meteorologia.uib.es/` (account `meteorologia`):

| folder | contents | public? |
|---|---|---|
| `web/dana/` | `index.html`, `static/`, `frames/` (maps), `api.php`, `.htaccess` | yes |
| `dana_app/` | PHP code, `settings.json`, `schema.sql`, `cases/` (manifest, evaluation files, overlays), `dev/` (test helpers) | no |
| `dana_data/` | `responses.sqlite` (answers, invite codes, code requests), `secret_key` (signs the login cookie and keys the email fingerprints) | no |

On `medicane`, in `expert_eval/`: the source code, the built case pool (`cases/`) and `host.sh`.
Everything is prepared on `medicane` and pushed to the host. Nothing is edited on the host directly.

### Requirements (on medicane)

- The SSH key `~/.ssh/meteo`, which the host accepts for `meteorologia@meteorologia.uib.es`.
  `~/.ssh/config` maps it, so `ssh meteorologia` logs in without a password. Plain
  `ssh meteorologia@meteorologia.uib.es` without that config (or from another machine) asks for
  the password, because SSH does not try non-default key names on its own. `host.sh` always passes
  the key explicitly.
- Python ≥ 3.11 as `python3` (for reading `settings.toml`); override with `PYTHON=/path/to/python`.
- The AtmoTrack conda environment, only for building cases.

### Step by step

Run everything from `expert_eval/` on `medicane`.

**1. Build or extend the case pool** (see section 1):

```bash
conda activate atmotrack
python build_cases.py --n-cases 600 --jobs 8      # first build; later runs append new cases
```

**2. Adjust the session settings (optional).** Edit `settings.toml`: `[session]` for session sizes
and the share of algorithm-DANA cases, `[sampling]` for how strongly disputed cases are favoured,
and `[review]` for the reason options. Changes take effect at the next deploy.

**3. Deploy** code, cases and maps:

```bash
./host.sh deploy
```

It uploads only what changed (the first upload of ~450 MB of maps takes a few minutes), creates
the database if it does not exist, and registers new cases. Existing answers, experts and invite
codes are kept, so re-deploying is safe at any time.

**4. Check it works.** Open https://meteorologia.uib.es/dana/ in a browser. Optionally run the PHP
test suite on the host (section Tests). It uses a throwaway database, not the live one.

**5. Codes: let experts request them, or hand them out.** Experts without a code click **Request a
code** on the sign-in page and fill in name, email, institution and experience. The site creates their
personal code at once and emails the request to `settings.toml [email] notify_address`
(`d.argueso@uib.es`). That email lists name, institution, experience, email and code, has the
requester as **Reply-To**, and includes a ready-made reply: press Reply, paste, send. If they lose
the code, they request again with the same address; you get a "[repeat]" email with the same code.
You can also create codes yourself, for people you invite directly; the label identifies them later
in `admin.py report`:

```bash
./host.sh admin add-codes --n 10 --label pilot    # prints CODE<TAB>label, e.g. K7Q2-9XPM  pilot-1
./host.sh admin add-codes --n 1 --label AEMET
```

Codes must be created this way, on the host database. `python admin.py add-codes` only writes to a
local database.

**5b. Send the codes automatically with Power Automate (set up once).** A cloud flow in the
`d.argueso@uib.es` Microsoft 365 account answers each request email from that mailbox, so no manual
reply is needed and no password is stored on the web host. Each request email carries, at the bottom,
a secret key and base64-encoded recipient and reply text (`[DANA-KEY]`, `[DANA-TO]`, `[DANA-BODY]`),
so the flow copies the ready-made reply without parsing text and ignores emails that lack the key.

1. Get the key: `./host.sh admin flow-key` (stored on the host as `dana_data/flow_key`).
2. In Outlook, create a folder **DANA requests**.
3. https://make.powerautomate.com → **Create → Automated cloud flow** → trigger **When a new email
   arrives (V3)** (Office 365 Outlook). Folder `Inbox`; under *Advanced parameters*: **From**
   `d.argueso@uib.es`, **Subject Filter** `DANA Expert Check: code request`, **Include Attachments** `No`.
4. Add a **Condition**: left value (fx)
   `contains(triggerOutputs()?['body/body'], '[DANA-KEY]<key>[/DANA-KEY]')`, *is equal to*, right
   value (fx) `true`.
5. In **True**, add **Send an email (V2)**:
   - **To** (fx): `base64ToString(trim(first(split(last(split(triggerOutputs()?['body/body'], '[DANA-TO]')), '[/DANA-TO]'))))`
   - **Subject**: `Your DANA Expert Check code`
   - **Body** (fx): `base64ToString(trim(first(split(last(split(triggerOutputs()?['body/body'], '[DANA-BODY]')), '[/DANA-BODY]'))))`
6. Below it, add **Move email (V2)**: **Message Id** (fx) `triggerOutputs()?['body/id']`, **Folder** `DANA requests`. Save.

If a code does not arrive, check the flow's **Run history**. If the flow never runs, remove the *From*
filter (the key check still protects it). Changing the key (deleting `dana_data/flow_key` on the host)
requires updating the Condition. Replies are sent as `d.argueso@uib.es` through Microsoft 365, which
external providers such as iCloud accept.

**6. Invite experts.** Send the link and a short note, for example:

> Please help us evaluate an automatic cut-off low (DANA) detector: https://meteorologia.uib.es/dana/
> Click "Request a code" and we will email you a personal code. Use a laptop or desktop. Each session shows 10 maps (you can choose more). You can end at
> any time, see how the algorithm compared with you, and come back later with the same code.

If you created a code for them with `add-codes`, include it instead of the "Request a code" line.

**7. Follow progress:**

```bash
./host.sh admin stats          # code requests, experts, sessions, answers, reviews, outcome counts
./host.sh admin requests       # requested codes: name, institution, experience, emails sent, used, answers
./host.sh admin list-codes     # all codes (requested and handed out), used or not, answers each
```

**8. Analyse the answers.** Copy them to `medicane`, then use the tools in sections 3 and 4:

```bash
./host.sh pull-db                                   # consistent snapshot -> data/responses_host.sqlite
export EXPERT_EVAL_DB=data/responses_host.sqlite
python enrich_responses.py                          # optional, AtmoTrack env
python admin.py report
python admin.py export-tuning tuning.csv
python admin.py sweep
```

`pull-db` briefly pauses writers while copying, so the snapshot is consistent even while experts are
answering. Always analyse the copy; never copy a local database back to the host.

### Other operations

| task | how |
|---|---|
| add more cases later | `python build_cases.py --n-cases 200`, then `./host.sh deploy`. New cases enter the sampling at once; answers stay |
| change the page or server code | edit `static/` or `php/`, run the tests, then `./host.sh deploy` |
| check that request emails get through | `./host.sh test-email d.argueso@uib.es` |
| change where requests go, the texts or limits | `settings.toml [email]` (`notify_address`, limits), `email_request.html` (request email), `email_reply.html` (the reply the expert receives) or `email_code.txt`, then `./host.sh deploy` |
| email codes straight to experts instead | needs a sender that external providers accept (e.g. an approved UIB service account): set `mode = "send"`, `transport = "smtp"` and the `smtp_*` settings, `./host.sh deploy`, then `./host.sh set-smtp` |
| take the site offline | `ssh meteorologia 'mv /srv/www/meteorologia.uib.es/web/dana /srv/www/meteorologia.uib.es/web/dana.off'` (reverse the `mv` to bring it back; answers are untouched; don't deploy while offline, it recreates the folder) |
| back up the answers | `./host.sh pull-db data/backup-$(date +%F).sqlite` |
| after tuning the algorithm | see section 2c: refresh the cases, deploy, `./host.sh admin rescore`, compare |
| start a new campaign with an empty database | `./host.sh archive LABEL` (section 2c); the old answers are kept locally and on the host |
| see who visited | Apache access log in `/srv/www/meteorologia.uib.es/logs/` (lines containing `/dana/`) |

### Good to know

- **Deploy from one place.** `host.sh deploy` mirrors `static/`, `php/` and `cases/` from the
  machine it runs on, so always deploy from the same, up-to-date checkout.
- **Host limits.** SQLite on the host is 3.7.17 and sits on a network disk, so the PHP code avoids
  newer SQL (no UPSERT) and uses SQLite's classic rollback journal. It handles a handful of experts
  answering at the same time without trouble.
- **SSH rate limit.** The university blocks addresses that open many SSH connections in a short
  time (for both SSH and the website, for several minutes). `host.sh` and the tests reuse a single
  connection. Avoid loops of separate `ssh` commands.
- **Login cookie.** Signed, valid for 90 days, scoped to `/dana/`, and marked Secure over HTTPS.
- **Code requests by email (`mode = "notify"`).** Requests are sent with PHP `mail()` through the
  web host's Postfix, which relays via the university SMTP server and delivers inside UIB, from
  `from_address` to `notify_address`. No password is stored anywhere. Limits: 3 emails per address per
  day, 10 requests per visitor per hour, 60 emails per hour in total. A hidden field catches simple
  bots, and the page never reveals whether an address already has a code.
- **Why not email the code straight to experts?** That is `mode = "send"`, still in the code. The
  university relay's IP is rejected by iCloud (`550 5.7.1 [HCM2]`), so external addresses cannot be
  reached reliably from the host. Using a mailbox login instead (`transport = "smtp"`, stored with
  `./host.sh set-smtp` in `dana_data/smtp_credentials.json`) was ruled out for now: Proton SMTP tokens
  need a paid plan with a custom domain, a new Gmail account needs a phone, and an app password for a
  personal Gmail would expose that whole mailbox if the host were compromised. An approved UIB sender
  from IT would make `mode = "send"` possible.
- **Privacy.** The site stores no email address. `code_requests` keeps name, institution, experience
  and an HMAC-SHA256 fingerprint of the address; `request_log` keeps fingerprints of address and visitor
  IP for 7 days, for the rate limits. The only copy of an address is the request email in the
  organisers' inbox.
- **Keep `dana_data/secret_key`.** It signs the login cookies and keys the fingerprints. If it is lost
  or replaced, everyone has to sign in again and a repeat request creates a new code instead of
  resending the old one.

## 2c. After tuning the algorithm, or starting a new campaign

The maps and the experts' answers (yes/no and clicks) do not depend on the algorithm; what the
algorithm detects does (`cases/eval/`, `cases/overlays/`, categories, tags) and so does every
outcome. Case ids are timestamps, so the same answers can be scored again against a tuned algorithm.

### A. Tune and re-score the answers already collected (normal workflow)

1. **Back up the answers:** `./host.sh pull-db data/archive/responses_before_<change>.sqlite`
2. **Tune and re-run the tracking** (in the repository): edit `config.toml` (or the code in
   `tracking/`), then `python COL_tracking_ERA5.py ...` so `data_tracking/col_z500_YYYY.nc` is rewritten.
3. **Refresh the case pool** (from `expert_eval/`, AtmoTrack environment):
   ```bash
   python build_cases.py --refresh --high-impact high_impact_events.toml --jobs 16
   ```
   It rescans the tracking files (the scan cache notices the change), recomputes for **every existing
   case** the algorithm objects, criteria, category, tags and overlay, and keeps case ids, maps and
   the high-impact tags. The log shows how many cases changed category (e.g. `nocol_borderline ->
   col_clear`) and the new **algorithm version** (a fingerprint of the thresholds, the last commit
   touching `tracking/`, and the tracking files), stored in `cases/manifest.json`.
   - Check the line `Criteria re-evaluation vs saved col_objects: 0/N records differ`. If the tuning
     changed the *logic* of `tracking/col.py` (not only thresholds), `criteria.py` must be updated to
     mirror it, otherwise that number is not 0 and the per-criterion diagnostics are wrong.
   - A normal build (without `--refresh`) warns when the tracking output changed but the cases were
     not refreshed.
4. **Deploy and re-score on the host:**
   ```bash
   ./host.sh deploy
   ./host.sh admin rescore
   ```
   `rescore` evaluates every stored answer against the refreshed cases. The original score of each
   answer is kept in `response_scores` under the version it was made with, the new one is added, and
   `responses` now holds the new score, so the site (summaries, disagreement-based sampling) and the
   reports use the tuned algorithm. It prints how many outcomes changed (e.g. `algo_miss ->
   agree_hit`). Running it again does nothing; `--force` recomputes anyway.
5. **Compare old and new on the same answers:**
   ```bash
   ./host.sh pull-db
   EXPERT_EVAL_DB=data/responses_host.sqlite python admin.py compare
   ```
   It shows, for the oldest and newest versions (`--old V --new V` to choose), agreement, expert
   DANA maps detected, systems located, false alarms and outcome counts, the list of outcome changes
   marked *better* / *worse*, and per-event agreement for the high-impact events. Unsure answers are
   left out unless `--include-unsure`.

Repeat 2–5 for each tuning step; every version stays in `response_scores`. For a local Flask site,
`python admin.py rescore` does step 4 on the local database.

### B. Start a new campaign (new question, box, period or pool)

Use this when you change what experts are asked, not just the algorithm, so old and new answers are
not mixed:

```bash
./host.sh archive campaign1                       # asks you to type the label to confirm
mv cases cases_campaign1                           # keep the old pool locally
python build_cases.py --high-impact high_impact_events.toml --target-total 1000 --jobs 16
./host.sh deploy
```

`archive` copies the answers to `data/archive/responses_campaign1.sqlite`, renames the host's
`dana_data` to `dana_data_campaign1` and starts an empty database, keeping `secret_key` and
`flow_key` (so the Power Automate flow keeps working). Existing codes stop working; experts request a
new one. `deploy` then replaces the maps and cases on the host with the new pool.

## 3. Analyse

For the live site, first copy the answers here with `./host.sh pull-db` and prefix the commands
below with `EXPERT_EVAL_DB=data/responses_host.sqlite`. For a local Flask site, the answers are in
`data/responses.sqlite` on the machine that runs `app.py` (safe to analyse while it runs). Keep a
single live copy of the site, otherwise answers end up split across databases.

```bash
python enrich_responses.py                # optional: criteria around each click (needs numpy, scipy)
python admin.py report                    # disagreement analysis printed to the terminal
python admin.py export responses.csv      # one row per answer
python admin.py export-tuning tuning.csv  # one row per (answer, cyclone object) with all criteria
python admin.py sweep                     # isolation threshold grid vs expert labels
python admin.py high-impact --csv events.csv  # verdicts on the high-impact events
python admin.py compare                   # algorithm versions compared on the same answers (section 2c)
python admin.py list-codes                # invite codes, whether used, answers per code
```

Results only become meaningful once each case has several answers: a case answered once has a
disagreement rate of 0 % or 100 %. `report --min-answers N` controls how many answers a case needs
to appear in the "most disputed" list.

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

## 4. Interpreting the results

### `admin.py report`

The report prints these sections, in order:

| section | what it shows | how to read it |
|---|---|---|
| **Outcomes** | count and share of each outcome over all answers | overall agreement is `agree_hit` + `agree_null` |
| **Disagreement by case category / tag / season / expert experience / expert** | `n` answers, % disagreement, % unsure per group | high disagreement *and* low unsure on a tag points to a systematic algorithm issue (e.g. `marginal_isolation`, `onset`). High unsure means experts find the situation genuinely ambiguous. One expert far from the rest may interpret the task differently |
| **Expert systems the algorithm did not detect** | for every expert click not matched to an algorithm COL: the first failed criterion of the nearest tracked z500 cyclone (within 300 km), how often each criterion failed, and how many had no tracked cyclone at all | the criterion that fails most often is the first candidate for relaxing. `(nearest object is a COL: location offset)` means the algorithm found the system but the click fell outside its area. `no tracked z500 cyclone` points at the cyclone detection step (`[cy_acy_z500]`), not the COL criteria. `col_duration` often fails as a *consequence* of other failures, so look at the first failed criterion |
| **Algorithm COLs: false alarms vs hits** | p10 / median / p90 of isolation margin, Δz margin (m), box margin (deg), poleward u200, z500 minimum (dam), area, COL lifetime and hours since onset / to decay, for COLs experts rejected (FA, maps answered "no") and COLs they confirmed (HIT) | where the FA distribution sits clearly below the HIT one (e.g. small isolation margins, short lifetimes, early onset), tightening that criterion should remove false alarms while keeping hits |
| **Location** | distance from expert clicks to the algorithm's z500 minimum for matched and mismatched clicks, the share of mismatched clicks inside the COL's parent cyclone, and the distance to the nearest COL grid cell | mismatched clicks inside the parent cyclone, or within a few hundred km of the COL area, suggest the COL area is too small or the centre is placed on a different lobe, rather than a wrong detection |
| **High-impact events** | one row per event in `high_impact_events.toml`: case time, algorithm result (COL or not), answers, % of experts saying DANA, % agreeing with the algorithm, % located inside the algorithm's area (when both saw a DANA), % unsure, the expert majority (DANA > 60 %, no DANA < 40 %, otherwise split) and whether the algorithm matches it; then a count of events where it does, events still short of answers, and reviewers' comments | these are the reference cases to agree on with the reviewers: an event the experts clearly call a DANA but the algorithm misses (or the other way round) weighs more than a random disagreement. `split` or high unsure means the event itself needs discussion before tuning on it |
| **Post-session reviews** | per outcome: how many experts kept or would change their answer, and the reasons they picked | "would change" answers are weaker evidence against the algorithm. Frequent reasons (e.g. *Open trough* for false alarms) suggest which criterion to revisit |
| **Most disputed cases** | case id and time, category, answers, disagreement %, outcome breakdown and tags | case ids are timestamps (`YYYYMMDDHH`). Look at them with `Plotting/plot_z500_t850_DANAS.py` or the frames in `cases/frames/` |

### `compare`

`python admin.py compare [--old V] [--new V] [--include-unsure]` compares two algorithm versions on
the answers scored under both (section 2c). **agreement** is the share of `agree_hit` + `agree_null`;
**expert DANA maps detected** is the share of maps where the expert saw a DANA and the algorithm
detected one (anywhere); **expert systems located** is the share of expert centres inside an algorithm
COL; **false alarms** is the share of the expert's "no DANA" maps where the algorithm detected one.
Outcome changes towards `agree_hit`/`agree_null` are *better*, away from them *worse*; a tuning step is
worth keeping when the better changes clearly outnumber the worse ones, especially on the high-impact
events.

### `high-impact`

`python admin.py high-impact [--min-answers 3] [--csv events.csv]` prints the high-impact table on its
own and optionally writes it as CSV (one row per event, including slide map times and all review
comments), ready to share with reviewers. Events need `--min-answers` answers before a verdict is shown.

### `export responses.csv`

One row per answer: expert (label, experience, affiliation), case (id, time, category, tags,
number of algorithm COLs), the answer (`has_dana`, `unsure`, `clicks` as lon/lat JSON), `outcome`,
system counts (`n_systems`, `n_matched`, `n_algo_missed`, `n_algo_extra`), `frames_viewed`
(hours relative to T+0), `response_ms`, the post-session review and `algo_version`.

### `export-tuning tuning.csv`

The table for tuning thresholds. Agreements are included, so it also shows which decisions to keep.

| column | meaning |
|---|---|
| `kind` | `cyclone_object`: a tracked z500 cyclone at the case time (a COL, near the box, or nearest to an expert click). `expert_click_no_cyclone`: an expert DANA with no tracked cyclone nearby (criteria filled in by `enrich_responses.py`) |
| `expert_dana` | 1 if the expert marked this system as a DANA (a click inside the COL, or an unmatched click within 300 km of this cyclone) |
| `algo_col` | 1 if the algorithm flagged it as a COL |
| `agree` | `expert_dana == algo_col` |
| `<criterion>_pass`, `<criterion>_value`, `<criterion>_margin` | for `cy_duration`, `latitude`, `pole`, `object_bounds`, `isolation`, `eastward_flow`, `z500_threshold`, `region`, `border`, `col_duration`. The margin is the distance to the threshold (negative = failed) |
| `isolation_dz_at_required_fraction`, `isolation_dz_margin`, `isolation_dz_median`, `isolation_dz_min`, `isolation_dz_q00` … `isolation_dz_q50` | Δz (m) between the ring and the centre. `dz_qXX` is the XX % quantile over ring points, so isolation passes when `dz_q{100·(1 − col_percent_isolation)}` > `col_thres_isolation` |
| `first_failed_criterion`, `failed_criteria` | in `col.py` order, pipe-separated |
| `zmin_lat`, `zmin_lon`, `zmin_dam`, `area_km2`, `cy_life_steps`, `step_in_cy_life` | descriptors of the system |
| `unsure`, `review_changed`, `experience`, `category`, `tags`, `outcome` | for filtering or weighting |

Example:

```python
import pandas as pd

df = pd.read_csv("tuning.csv")
obj = df[(df.kind == "cyclone_object") & (df.unsure == 0)]
# how isolation differs between expert-confirmed and rejected systems
print(
    obj.groupby(["expert_dana", "algo_col"])[["isolation_value", "isolation_dz_margin"]].describe()
)
# which criteria reject systems experts consider DANAs
print(obj[(obj.expert_dana == 1) & (obj.algo_col == 0)].first_failed_criterion.value_counts())
```

### `sweep`

Re-scores the stored criteria against the expert labels (`tuning.csv` rows) for a grid of
`col_percent_isolation` (0.60–0.95) × `col_thres_isolation` (20–140 m), at the current
`col_min_dur` plus any extra `--durations`. Unsure answers are excluded unless `--include-unsure`.
Rows are sorted by CSI and the current setting is marked.

| column | meaning |
|---|---|
| `hit` / `miss` / `fa` / `cn` | expert DANA detected / expert DANA not detected / algorithm COL rejected by the expert / both say no |
| `CSI` | hit / (hit + miss + fa): overall skill, 1 is perfect |
| `POD` | hit / (hit + miss): share of expert DANAs detected |
| `FAR` | fa / (hit + fa): share of algorithm COLs experts reject |

Expert DANAs with no tracked cyclone always count as misses, whatever the thresholds. All other
criteria keep their stored pass/fail, and duration is only approximated from stored lifetimes.
Use the sweep as a hint: change `config.toml`, re-run the tracking and rebuild the case pool (see
section 1) to confirm.

### `enrich_responses.py`

For every expert click, finds the lowest z500 point within 300 km and evaluates ring isolation,
poleward u200 and box membership there, using the fields saved in `cases/eval/fields/`. Results go
to the `click_diagnostics` table and feed the `expert_click_no_cyclone` rows of `tuning.csv`. Run it
before `report` / `export-tuning`. Only new clicks are processed; `--all` recomputes everything.
Needs numpy and scipy (`pip install numpy scipy`).

### Direct database access

```bash
sqlite3 data/responses.sqlite
```

```sql
-- answers and disagreement rate per case (sampler uses the same view)
SELECT * FROM case_agreement WHERE n > 0 ORDER BY disagree_rate DESC, n DESC LIMIT 20;
-- outcomes per expert
SELECT expert_id, outcome, COUNT(*) FROM responses GROUP BY expert_id, outcome;
```

Tables: `experts`, `invite_codes`, `sessions`, `responses` (`click_details` and `algo_details`
are JSON), `reviews`, `click_diagnostics`, `cases`.

## Tests

```bash
EXPERT_EVAL_CASES_DIR=cases python -m pytest -q test_expert_eval.py
```

`EXPERT_EVAL_CASES_DIR`, `EXPERT_EVAL_DB` and `EXPERT_EVAL_SETTINGS` override the paths in settings.toml.

The PHP server is tested against the Python code. Run it on the host after deploying, so it uses the
host's own PHP and SQLite. It opens a temporary PHP server there through an SSH port forward, with a
throwaway database next to the real one that is removed afterwards:

```bash
EXPERT_EVAL_CASES_DIR=cases PHP_SSH_HOST=meteorologia@meteorologia.uib.es PHP_SSH_KEY=~/.ssh/meteo \
  python -m pytest -q test_php_backend.py
```

It checks that `evaluate.php` matches `evaluate.py` on hundreds of clicks over real cases, the sampler's
properties, and the full expert flow of `test_expert_eval.api_flow`. With a local PHP that has the
zlib extension, use `PHP_BIN=/path/to/php` instead of the SSH variables.
