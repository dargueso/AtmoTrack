<?php
/**
 * api.php — request router for the DANA expert evaluation site (PHP port of app.py).
 *
 * Reached through <site>/web/dana/api.php, with .htaccess rewriting /dana/api/<route> to
 * api.php?r=<route>. Endpoints and JSON shapes are identical to the Flask app.
 */

declare(strict_types=1);

require_once __DIR__ . '/bootstrap.php';

set_exception_handler(function (Throwable $e) {
    error_log('[dana] ' . $e->getMessage() . ' @ ' . $e->getFile() . ':' . $e->getLine());
    if (!headers_sent()) {
        json_out(['error' => 'Server error, please try again'], 500);
    }
});

const REASON_KEYS = [
    'algo_false_alarm' => 'reasons_false_alarm',
    'algo_miss' => 'reasons_miss',
    'location_mismatch' => 'reasons_location',
];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function current_expert(): ?array
{
    $eid = cookie_expert_id();
    return $eid === null ? null : q1(db(), 'SELECT * FROM experts WHERE id = ?', [$eid]);
}

function require_expert(): array
{
    $ex = current_expert();
    if ($ex === null) {
        fail(401, 'Please sign in');
    }
    return $ex;
}

function expert_public(array $ex): array
{
    return [
        'id' => (int) $ex['id'],
        'auth' => $ex['auth'],
        'name' => $ex['name'],
        'label' => $ex['name'] ?: $ex['code'],
    ];
}

function case_payload(array $m, string $cid): array
{
    $c = $m['cases'][$cid];
    return [
        'case_id' => $cid,
        'month' => $c['month'],
        'frames' => array_map(fn($f) => "frames/$f.png", $c['frames']),
        'center_index' => $c['center_index'],
        'dt_hours' => $c['dt_hours'],
    ];
}

function own_session(int $sid, array $ex): array
{
    $row = q1(db(), 'SELECT * FROM sessions WHERE id = ?', [$sid]);
    if ($row === null || (int) $row['expert_id'] !== (int) $ex['id']) {
        fail(404, 'Session not found');
    }
    return $row;
}

function draw_for(array $ex, int $n, array $extraExclude = []): array
{
    $m = manifest();
    $rows = qall(db(), 'SELECT c.case_id, c.category, c.tags, c.algo_n_cols, a.n, a.n_disagree, a.n_unsure
                        FROM cases c JOIN case_agreement a ON a.case_id = c.case_id');
    $rows = array_filter($rows, fn($r) => isset($m['cases'][$r['case_id']]));
    $exclude = [];
    foreach (qall(db(), 'SELECT case_id FROM responses WHERE expert_id = ?', [$ex['id']]) as $r) {
        $exclude[(string) $r['case_id']] = true;
    }
    foreach ($extraExclude as $cid) {
        $exclude[(string) $cid] = true;
    }
    return draw_session($rows, $n, $exclude, settings());
}

function reasons_for(string $outcome): array
{
    $r = settings()['review'];
    if ($outcome === 'partial_match') {
        return array_merge(array_slice($r['reasons_location'], 0, -1), [
            'Algorithm missed one of my systems',
            'Algorithm found a system I do not consider a DANA',
            'Other',
        ]);
    }
    return isset(REASON_KEYS[$outcome]) ? $r[REASON_KEYS[$outcome]] : [];
}

function clamp_cases($v, int $default): int
{
    $n = is_numeric($v) && (int) $v > 0 ? (int) $v : $default;
    return max(1, min($n, 100));
}

// ---------------------------------------------------------------------------
// Handlers
// ---------------------------------------------------------------------------
function h_config(): void
{
    $m = manifest();
    $ex = current_expert();
    $s = settings()['session'];
    json_out([
        'geometry' => $m['geometry'],
        'box' => $m['box'],
        'loop_hours' => $m['loop_hours'] ?? null,
        'n_cases_pool' => count($m['cases']),
        'session_sizes' => $s['size_options'],
        'default_cases' => $s['default_cases'],
        'extend_by' => $s['extend_by'],
        'experience_options' => EXPERIENCE,
        'request_mode' => settings()['email']['mode'] ?? 'send',
        'expert' => $ex ? expert_public($ex) : null,
    ]);
}

function h_login(): void
{
    $b = request_body();
    $pdo = db();
    $code = strtoupper(trim((string) ($b['code'] ?? '')));
    if ($code === '') {
        fail(400, 'Please enter your invite code');
    }
    $row = q1($pdo, 'SELECT * FROM invite_codes WHERE code = ?', [$code]);
    if ($row === null) {
        fail(400, 'Unknown invite code');
    }
    if ($row['expert_id'] === null) {
        // first use: the expert takes the details given when the code was requested, if any
        $req = q1($pdo, 'SELECT name, affiliation, experience FROM code_requests WHERE code = ?', [$code]);
        $pdo->beginTransaction();
        qexec($pdo, "INSERT INTO experts (auth, code, name, affiliation, experience, created_at)
                     VALUES ('code', ?, ?, ?, ?, ?)",
            [$code, $req['name'] ?? $row['label'], $req['affiliation'] ?? null, $req['experience'] ?? null, now_iso()]);
        $eid = (int) $pdo->lastInsertId();
        qexec($pdo, 'UPDATE invite_codes SET expert_id = ? WHERE code = ?', [$eid, $code]);
        $pdo->commit();
    } else {
        $eid = (int) $row['expert_id'];
    }
    set_auth_cookie($eid);
    json_out(['expert' => expert_public(q1($pdo, 'SELECT * FROM experts WHERE id = ?', [$eid]))]);
}

const REQUEST_OK = 'Thank you. If the address is valid, your code is on its way: please check your inbox (and the spam folder).';
const REQUEST_OK_NOTIFY = 'Thank you. We have received your request and will email you your personal code shortly.';

function request_ok_message(): string
{
    return (settings()['email']['mode'] ?? 'send') === 'notify' ? REQUEST_OK_NOTIFY : REQUEST_OK;
}
const REQUEST_LIMITED = 'Too many requests. Please try again later.';

/**
 * Request a personal code by email. The address is used to send the message and then discarded:
 * only a keyed fingerprint is stored, so a repeat request resends the same code.
 */
function h_request_code(): void
{
    $b = request_body();
    if (trim((string) ($b['website'] ?? '')) !== '') {  // hidden spam-trap field
        json_out(['ok' => true, 'message' => request_ok_message()]);
    }
    $squash = fn($v) => trim((string) preg_replace('/\s+/u', ' ', (string) $v));
    $name = $squash($b['name'] ?? '');
    $affiliation = $squash($b['affiliation'] ?? '');
    $experience = trim((string) ($b['experience'] ?? ''));
    $email = strtolower(trim((string) ($b['email'] ?? '')));
    if ($name === '' || $affiliation === '' || !in_array($experience, EXPERIENCE, true)
        || strlen($name) > 120 || strlen($affiliation) > 200) {
        fail(400, 'Please give your name, institution and experience');
    }
    if (strlen($email) > 254 || !filter_var($email, FILTER_VALIDATE_EMAIL)) {
        fail(400, 'Please give a valid email address');
    }

    $cfg = settings()['email'];
    $pdo = db();
    $ipRaw = trim(explode(',', (string) ($_SERVER['HTTP_X_FORWARDED_FOR'] ?? ''))[0]) ?: ($_SERVER['REMOTE_ADDR'] ?? '');
    $ipHash = fingerprint('ip:' . $ipRaw);
    $emailHash = fingerprint('email:' . $email);
    $count = fn(string $sql, array $p) => (int) q1($pdo, $sql, $p)['n'];

    qexec($pdo, 'DELETE FROM request_log WHERE created_at < ?', [iso_ago(7 * 86400)]);
    $limited = $count('SELECT COUNT(*) AS n FROM request_log WHERE ip_hash = ? AND created_at >= ?', [$ipHash, iso_ago(3600)]) >= $cfg['max_per_ip_per_hour']
        || $count('SELECT COUNT(*) AS n FROM request_log WHERE email_hash = ? AND sent = 1 AND created_at >= ?', [$emailHash, iso_ago(86400)]) >= $cfg['max_per_email_per_day']
        || $count('SELECT COUNT(*) AS n FROM request_log WHERE sent = 1 AND created_at >= ?', [iso_ago(3600)]) >= $cfg['max_total_per_hour'];
    qexec($pdo, 'INSERT INTO request_log (created_at, ip_hash, email_hash, sent) VALUES (?, ?, ?, 0)', [now_iso(), $ipHash, $emailHash]);
    if ($limited) {
        fail(429, REQUEST_LIMITED);
    }

    $pdo->beginTransaction();
    try {
        $existing = q1($pdo, 'SELECT id, code, name, affiliation, experience, n_sent FROM code_requests WHERE email_hash = ?', [$emailHash]);
        if ($existing) {
            $code = $existing['code'];
            $greet = $existing['name'];
            [$affiliation, $experience] = [(string) $existing['affiliation'], $existing['experience']];
            $nSent = (int) $existing['n_sent'] + 1;
            qexec($pdo, 'UPDATE code_requests SET last_sent_at = ?, n_sent = n_sent + 1 WHERE id = ?', [now_iso(), $existing['id']]);
        } else {
            $code = new_invite_code($pdo);
            $greet = $name;
            $nSent = 1;
            qexec($pdo, 'INSERT INTO invite_codes (code, label, created_at) VALUES (?, ?, ?)', [$code, $name, now_iso()]);
            qexec($pdo, 'INSERT INTO code_requests (email_hash, code, name, affiliation, experience, created_at, last_sent_at, n_sent)
                         VALUES (?, ?, ?, ?, ?, ?, ?, 1)', [$emailHash, $code, $name, $affiliation, $experience, now_iso(), now_iso()]);
        }
        if (($cfg['mode'] ?? 'send') === 'notify') {
            send_request_notification($email, $greet, $affiliation, $experience, $code, $nSent);
        } else {
            send_code_email($email, $greet, $code);
        }
        qexec($pdo, 'INSERT INTO request_log (created_at, ip_hash, email_hash, sent) VALUES (?, ?, ?, 1)', [now_iso(), $ipHash, $emailHash]);
        $pdo->commit();
    } catch (Throwable $e) {
        $pdo->rollBack();
        error_log('[dana] code request failed: ' . $e->getMessage());
        $contact = sender_address($cfg);
        fail(500, 'We could not send the email right now. Please try again later' . ($contact ? " or write to $contact" : '') . '.');
    }
    json_out(['ok' => true, 'message' => request_ok_message()]);
}

function h_logout(): void
{
    set_auth_cookie(null);
    json_out(['ok' => true]);
}

function h_new_session(): void
{
    $ex = require_expert();
    $m = manifest();
    $n = clamp_cases(request_body()['n_cases'] ?? null, (int) settings()['session']['default_cases']);
    [$ids, $nPos] = draw_for($ex, $n);
    if (!$ids) {
        fail(409, 'You have already answered every case in the pool. Thank you!');
    }
    $algoVersion = jenc(['version' => $m['algo_version'] ?? null, 'git_hash' => $m['git_hash'] ?? null,
                         'col_params' => $m['col_params'] ?? null]);
    $pdo = db();
    qexec($pdo, 'INSERT INTO sessions (expert_id, n_requested, n_pos_planned, case_order, algo_version, started_at)
                 VALUES (?, ?, ?, ?, ?, ?)', [$ex['id'], $n, $nPos, jenc($ids), $algoVersion, now_iso()]);
    $sid = (int) $pdo->lastInsertId();
    json_out(['session_id' => $sid, 'cases' => array_map(fn($c) => case_payload($m, $c), $ids)]);
}

function h_extend_session(int $sid): void
{
    $ex = require_expert();
    $m = manifest();
    $row = own_session($sid, $ex);
    $order = json_decode($row['case_order'], true);
    $n = clamp_cases(request_body()['n_cases'] ?? null, (int) settings()['session']['extend_by']);
    [$newIds, $nPos] = draw_for($ex, $n, $order);
    if (!$newIds) {
        fail(409, 'No more unseen cases in the pool. Thank you!');
    }
    qexec(db(), 'UPDATE sessions SET case_order = ?, n_requested = n_requested + ?,
                 n_pos_planned = n_pos_planned + ?, ended_at = NULL, end_reason = NULL WHERE id = ?',
        [jenc(array_merge($order, $newIds)), count($newIds), $nPos, $sid]);
    json_out([
        'session_id' => $sid,
        'offset' => count($order),
        'cases' => array_map(fn($c) => case_payload($m, $c), $newIds),
    ]);
}

function h_answer(): void
{
    $ex = require_expert();
    $b = request_body();
    $row = own_session(is_numeric($b['session_id'] ?? null) ? (int) $b['session_id'] : -1, $ex);
    $order = json_decode($row['case_order'], true);
    $cid = (string) ($b['case_id'] ?? '');
    $pos = array_search($cid, $order, true);
    if ($pos === false) {
        fail(400, 'Case not in this session');
    }
    $hasDana = !empty($b['has_dana']);
    $clicks = [];
    foreach (($b['clicks'] ?? []) ?: [] as $c) {
        if (!is_array($c) || !is_numeric($c['lat'] ?? null) || !is_numeric($c['lon'] ?? null)) {
            fail(400, 'Bad click');
        }
        $clicks[] = ['lat' => (float) $c['lat'], 'lon' => (float) $c['lon']];
    }
    if ($hasDana && !$clicks) {
        fail(400, 'Mark the centre of each DANA on the map');
    }
    $res = evaluate_answer(CaseData::load($cid), $hasDana, $clicks);
    $ms = is_numeric($b['response_ms'] ?? null) ? (int) $b['response_ms'] : 0;
    $pdo = db();
    try {
        qexec($pdo, 'INSERT INTO responses (session_id, expert_id, case_id, position, has_dana, unsure,
                        clicks, click_details, algo_details, outcome, n_systems, n_matched,
                        n_algo_missed, n_algo_extra, frames_viewed, response_ms, created_at)
                     VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', [
            $row['id'], $ex['id'], $cid, $pos, (int) $hasDana, (int) !empty($b['unsure']),
            jenc($hasDana ? $clicks : []), jenc($res['click_details']), jenc($res['algo_details']),
            $res['outcome'], $res['n_systems'], $res['n_matched'], $res['n_algo_missed'],
            $res['n_algo_extra'], jenc(($b['frames_viewed'] ?? []) ?: []), $ms ?: null, now_iso(),
        ]);
    } catch (PDOException $e) {
        if (str_starts_with((string) $e->getCode(), '23')) {
            fail(409, 'This case was already answered');
        }
        throw $e;
    }
    $nDone = (int) q1($pdo, 'SELECT COUNT(*) AS n FROM responses WHERE session_id = ?', [$row['id']])['n'];
    json_out(['ok' => true, 'n_answered' => $nDone, 'n_total' => count($order)]);
}

function summary(int $sid, array $ex): array
{
    $pdo = db();
    $row = own_session($sid, $ex);
    $m = manifest();
    $resp = qall($pdo, 'SELECT r.*, c.algo_n_cols, v.changed, v.reasons, v.comment
                        FROM responses r JOIN cases c ON c.case_id = r.case_id
                        LEFT JOIN reviews v ON v.response_id = r.id
                        WHERE r.session_id = ? ORDER BY r.position', [$sid]);
    $yes = array_filter($resp, fn($r) => (int) $r['has_dana'] === 1);
    $no = array_filter($resp, fn($r) => (int) $r['has_dana'] === 0);
    $byOutcome = [];
    $dis = [];
    foreach ($resp as $r) {
        $byOutcome[$r['outcome']] = ($byOutcome[$r['outcome']] ?? 0) + 1;
        if (in_array($r['outcome'], AGREE, true)) {
            continue;
        }
        $c = $m['cases'][$r['case_id']] ?? null;
        $dis[] = [
            'response_id' => (int) $r['id'],
            'case_id' => (string) $r['case_id'],
            'outcome' => $r['outcome'],
            'month' => $c['month'] ?? null,
            'frame' => $c ? 'frames/' . $c['frames'][$c['center_index']] . '.png' : null,
            'overlay' => 'api/overlay/' . $r['case_id'] . '.png',
            'clicks' => json_decode($r['clicks'], true),
            'click_matched' => array_map(fn($d) => $d['matched_col_id'] !== null, json_decode($r['click_details'], true)),
            'unsure' => (bool) $r['unsure'],
            'n_matched' => (int) $r['n_matched'],
            'n_algo_missed' => (int) $r['n_algo_missed'],
            'n_algo_extra' => (int) $r['n_algo_extra'],
            'algo_n_cols' => (int) $r['algo_n_cols'],
            'reason_options' => reasons_for($r['outcome']),
            'review' => $r['reasons'] !== null ? [
                'changed' => $r['changed'] === null ? null : (int) $r['changed'],
                'reasons' => json_decode($r['reasons'] ?: '[]', true),
                'comment' => $r['comment'],
            ] : null,
        ];
    }
    $overall = q1($pdo, "SELECT COUNT(*) AS n, SUM(outcome IN ('agree_hit','agree_null')) AS agree FROM responses");
    $sum = fn(iterable $rows, string $k) => array_sum(array_map(fn($r) => (int) $r[$k], is_array($rows) ? $rows : iterator_to_array($rows)));
    return [
        'session_id' => $sid,
        'end_reason' => $row['end_reason'],
        'n_planned' => count(json_decode($row['case_order'], true)),
        'n_answered' => count($resp),
        'n_unsure' => $sum($resp, 'unsure'),
        'expert_yes_maps' => count($yes),
        'expert_systems' => $sum($yes, 'n_systems'),
        'algo_found_maps' => count(array_filter($yes, fn($r) => (int) $r['algo_n_cols'] > 0)),
        'systems_located' => $sum($yes, 'n_matched'),
        'expert_no_maps' => count($no),
        'algo_flagged_maps' => count(array_filter($no, fn($r) => (int) $r['algo_n_cols'] > 0)),
        'algo_extra_systems' => $sum($yes, 'n_algo_extra'),
        'agree_maps' => count(array_filter($resp, fn($r) => in_array($r['outcome'], AGREE, true))),
        'by_outcome' => $byOutcome ?: new stdClass(),
        'disagreements' => $dis,
        'geometry' => $m['geometry'],
        'all_experts' => [
            'n_responses' => (int) $overall['n'],
            'agreement_rate' => (int) $overall['n'] ? ((int) $overall['agree']) / (int) $overall['n'] : null,
        ],
    ];
}

function h_end_session(int $sid): void
{
    $ex = require_expert();
    $row = own_session($sid, $ex);
    $pdo = db();
    $nDone = (int) q1($pdo, 'SELECT COUNT(*) AS n FROM responses WHERE session_id = ?', [$sid])['n'];
    $reason = $nDone >= count(json_decode($row['case_order'], true)) ? 'completed' : 'ended_early';
    qexec($pdo, 'UPDATE sessions SET ended_at = ?, end_reason = ? WHERE id = ?', [now_iso(), $reason, $sid]);
    json_out(summary($sid, $ex));
}

function h_review(): void
{
    $ex = require_expert();
    $b = request_body();
    $pdo = db();
    $rid = is_numeric($b['response_id'] ?? null) ? (int) $b['response_id'] : -1;
    $r = q1($pdo, 'SELECT id, outcome FROM responses WHERE id = ? AND expert_id = ?', [$rid, $ex['id']]);
    if ($r === null) {
        fail(404, 'Answer not found');
    }
    $allowed = reasons_for($r['outcome']);
    $reasons = array_values(array_filter((array) ($b['reasons'] ?? []), fn($x) => in_array($x, $allowed, true)));
    $changed = array_key_exists('changed', $b) && $b['changed'] !== null ? (int) (bool) $b['changed'] : null;
    $text = trim((string) ($b['comment'] ?? ''));
    $comment = (function_exists('mb_substr') ? mb_substr($text, 0, 2000) : substr($text, 0, 2000)) ?: null;
    $params = [$changed, jenc($reasons), $comment, now_iso(), $r['id']];
    // no UPSERT on SQLite 3.7.17: update, then insert if there was nothing to update
    $pdo->beginTransaction();
    if (qexec($pdo, 'UPDATE reviews SET changed = ?, reasons = ?, comment = ?, created_at = ? WHERE response_id = ?', $params) === 0) {
        qexec($pdo, 'INSERT INTO reviews (changed, reasons, comment, created_at, response_id) VALUES (?, ?, ?, ?, ?)', $params);
    }
    $pdo->commit();
    json_out(['ok' => true]);
}

function h_overlay(string $caseId): void
{
    $ex = require_expert();
    if (!q1(db(), 'SELECT 1 AS ok FROM responses WHERE expert_id = ? AND case_id = ?', [$ex['id'], $caseId])) {
        fail(403, 'Answer the case first');
    }
    $p = DANA_CASES . "/overlays/$caseId.png";
    if (!is_file($p)) {
        fail(404, 'Not found');
    }
    header('Content-Type: image/png');
    header('Cache-Control: private, max-age=86400');
    header('Content-Length: ' . filesize($p));
    readfile($p);
    exit;
}

// ---------------------------------------------------------------------------
// Router
// ---------------------------------------------------------------------------
$route = trim((string) ($_GET['r'] ?? ''), '/');
$method = $_SERVER['REQUEST_METHOD'] ?? 'GET';

if ($method === 'GET' && $route === 'config') {
    h_config();
} elseif ($method === 'GET' && preg_match('#^overlay/(\d{10})\.png$#', $route, $mm)) {
    h_overlay($mm[1]);
} elseif ($method === 'POST' && $route === 'login') {
    h_login();
} elseif ($method === 'POST' && $route === 'request-code') {
    h_request_code();
} elseif ($method === 'POST' && $route === 'logout') {
    h_logout();
} elseif ($method === 'POST' && $route === 'session') {
    h_new_session();
} elseif ($method === 'POST' && preg_match('#^session/(\d+)/extend$#', $route, $mm)) {
    h_extend_session((int) $mm[1]);
} elseif ($method === 'POST' && preg_match('#^session/(\d+)/end$#', $route, $mm)) {
    h_end_session((int) $mm[1]);
} elseif ($method === 'POST' && $route === 'answer') {
    h_answer();
} elseif ($method === 'POST' && $route === 'review') {
    h_review();
}
fail(404, 'Not found');
