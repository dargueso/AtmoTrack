-- schema.sql — shared by db.py (Python) and php/app/db.php (PHP).
-- Keep it compatible with SQLite 3.7.17 (the web host): no UPSERT, no JSON functions.
CREATE TABLE IF NOT EXISTS invite_codes (
    code        TEXT PRIMARY KEY,
    label       TEXT,
    created_at  TEXT NOT NULL,
    expert_id   INTEGER REFERENCES experts(id)
);
CREATE TABLE IF NOT EXISTS experts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    auth        TEXT NOT NULL CHECK (auth IN ('code', 'profile')),
    code        TEXT UNIQUE,
    name        TEXT,
    affiliation TEXT,
    experience  TEXT,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cases (
    case_id     TEXT PRIMARY KEY,
    time        TEXT NOT NULL,
    category    TEXT NOT NULL,
    tags        TEXT NOT NULL,
    algo_n_cols INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    expert_id     INTEGER NOT NULL REFERENCES experts(id),
    n_requested   INTEGER NOT NULL,
    n_pos_planned INTEGER NOT NULL,
    case_order    TEXT NOT NULL,
    algo_version  TEXT,
    started_at    TEXT NOT NULL,
    ended_at      TEXT,
    end_reason    TEXT CHECK (end_reason IN ('completed', 'ended_early'))
);
CREATE TABLE IF NOT EXISTS responses (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id     INTEGER NOT NULL REFERENCES sessions(id),
    expert_id      INTEGER NOT NULL REFERENCES experts(id),
    case_id        TEXT NOT NULL REFERENCES cases(case_id),
    position       INTEGER NOT NULL,
    has_dana       INTEGER NOT NULL,
    unsure         INTEGER NOT NULL DEFAULT 0,
    clicks         TEXT NOT NULL,
    click_details  TEXT NOT NULL,
    algo_details   TEXT NOT NULL,
    outcome        TEXT NOT NULL,
    n_systems      INTEGER NOT NULL,
    n_matched      INTEGER NOT NULL,
    n_algo_missed  INTEGER NOT NULL,
    n_algo_extra   INTEGER NOT NULL,
    frames_viewed  TEXT,
    response_ms    INTEGER,
    created_at     TEXT NOT NULL,
    UNIQUE (session_id, case_id)
);
CREATE INDEX IF NOT EXISTS idx_responses_case ON responses(case_id);
CREATE INDEX IF NOT EXISTS idx_responses_expert ON responses(expert_id);
CREATE TABLE IF NOT EXISTS reviews (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    response_id INTEGER NOT NULL UNIQUE REFERENCES responses(id),
    changed     INTEGER,
    reasons     TEXT NOT NULL DEFAULT '[]',
    comment     TEXT,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS click_diagnostics (
    response_id  INTEGER NOT NULL REFERENCES responses(id),
    click_idx    INTEGER NOT NULL,
    diagnostics  TEXT NOT NULL,
    algo_version TEXT,
    computed_at  TEXT NOT NULL,
    PRIMARY KEY (response_id, click_idx)
);
CREATE VIEW IF NOT EXISTS case_agreement AS
SELECT c.case_id,
       c.category,
       COUNT(r.id)                                                        AS n,
       COALESCE(SUM(r.outcome NOT IN ('agree_hit', 'agree_null')), 0)     AS n_disagree,
       COALESCE(SUM(r.unsure), 0)                                         AS n_unsure,
       CASE WHEN COUNT(r.id) > 0
            THEN 1.0 * SUM(r.outcome NOT IN ('agree_hit', 'agree_null')) / COUNT(r.id)
       END                                                                AS disagree_rate
FROM cases c LEFT JOIN responses r ON r.case_id = c.case_id
GROUP BY c.case_id;
