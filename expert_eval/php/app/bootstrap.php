<?php
/**
 * bootstrap.php — paths, settings, JSON helpers and the signed login cookie.
 *
 * Deployed layout on the web host (see ../../host.sh):
 *   <site>/web/dana/        public: index.html, static/, frames/, api.php, .htaccess
 *   <site>/dana_app/        this code, settings.json, schema.sql, cases/ (manifest, eval, overlays)
 *   <site>/dana_data/       responses.sqlite, secret_key (never served)
 *
 * Environment overrides (used for local testing with `php -S`):
 *   DANA_APP_DIR, DANA_CASES_DIR, DANA_DATA_DIR, DANA_SETTINGS
 */

declare(strict_types=1);

define('DANA_APP', rtrim(getenv('DANA_APP_DIR') ?: __DIR__, '/'));
define('DANA_CASES', rtrim(getenv('DANA_CASES_DIR') ?: DANA_APP . '/cases', '/'));
define('DANA_DATA', rtrim(getenv('DANA_DATA_DIR') ?: dirname(DANA_APP) . '/dana_data', '/'));
define('DANA_SETTINGS', getenv('DANA_SETTINGS') ?: DANA_APP . '/settings.json');
define('DANA_SCHEMA', getenv('DANA_SCHEMA') ?: DANA_APP . '/schema.sql');
define('DANA_EMAIL_TEMPLATE', getenv('DANA_EMAIL_TEMPLATE') ?: DANA_APP . '/email_code.txt');
define('DANA_MAIL_OUTBOX', getenv('DANA_MAIL_OUTBOX') ?: '');  // tests: write emails here instead of sending

const EXPERIENCE = ['Operational forecaster', 'Researcher', 'Student / early career', 'Other'];
const AGREE = ['agree_hit', 'agree_null'];
const COOKIE_NAME = 'dana_auth';
const COOKIE_DAYS = 90;

ini_set('serialize_precision', '-1');  // shortest float representation in json_encode

require_once __DIR__ . '/db.php';
require_once __DIR__ . '/mail.php';
require_once __DIR__ . '/evaluate.php';
require_once __DIR__ . '/sampler.php';

function settings(): array
{
    static $s = null;
    if ($s === null) {
        $s = json_decode((string) file_get_contents(DANA_SETTINGS), true, 512, JSON_THROW_ON_ERROR);
    }
    return $s;
}

function now_iso(): string
{
    return gmdate('Y-m-d\TH:i:s') . '+00:00';
}

function json_out($data, int $status = 200): void
{
    http_response_code($status);
    header('Content-Type: application/json');
    header('Cache-Control: no-store');
    echo json_encode($data, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE);
    exit;
}

function fail(int $status, string $message): void
{
    json_out(['error' => $message], $status);
}

function request_body(): array
{
    static $b = null;
    if ($b === null) {
        $raw = (string) file_get_contents('php://input');
        $b = $raw === '' ? [] : json_decode($raw, true);
        if (!is_array($b)) {
            $b = [];
        }
    }
    return $b;
}

function jenc($v): string
{
    return json_encode($v, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE);
}

// ---------------------------------------------------------------------------
// Signed login cookie: "<expert_id>.<expiry>.<hmac>"
// ---------------------------------------------------------------------------
function secret_key(): string
{
    $p = DANA_DATA . '/secret_key';
    if (!is_file($p)) {
        ensure_data_dir();
        file_put_contents($p, bin2hex(random_bytes(32)), LOCK_EX);
        chmod($p, 0600);
    }
    return trim((string) file_get_contents($p));
}

function cookie_path(): string
{
    $env = getenv('DANA_COOKIE_PATH');
    if ($env) {
        return $env;
    }
    $dir = str_replace('\\', '/', dirname($_SERVER['SCRIPT_NAME'] ?? '/'));
    return rtrim($dir, '/') . '/';
}

/** Keyed fingerprint (HMAC-SHA256) — used so emails and IPs are never stored in clear. */
function fingerprint(string $value): string
{
    return hash_hmac('sha256', $value, secret_key());
}

function new_invite_code(PDO $pdo): string
{
    $alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';
    do {
        $code = '';
        for ($c = 0; $c < 8; $c++) {
            $code .= ($c === 4 ? '-' : '') . $alphabet[random_int(0, strlen($alphabet) - 1)];
        }
    } while (q1($pdo, 'SELECT 1 AS x FROM invite_codes WHERE code = ?', [$code]));
    return $code;
}

function iso_ago(int $seconds): string
{
    return gmdate('Y-m-d\TH:i:s', time() - $seconds) . '+00:00';
}

function set_auth_cookie(?int $expertId): void
{
    $secure = !empty($_SERVER['HTTPS']) && $_SERVER['HTTPS'] !== 'off';
    $opts = ['path' => cookie_path(), 'httponly' => true, 'samesite' => 'Lax', 'secure' => $secure];
    if ($expertId === null) {
        setcookie(COOKIE_NAME, '', ['expires' => time() - 3600] + $opts);
        return;
    }
    $exp = time() + COOKIE_DAYS * 86400;
    $payload = $expertId . '.' . $exp;
    $sig = hash_hmac('sha256', $payload, secret_key());
    setcookie(COOKIE_NAME, $payload . '.' . $sig, ['expires' => $exp] + $opts);
}

function cookie_expert_id(): ?int
{
    $c = $_COOKIE[COOKIE_NAME] ?? '';
    if (!preg_match('/^(\d+)\.(\d+)\.([0-9a-f]{64})$/', $c, $m)) {
        return null;
    }
    $expected = hash_hmac('sha256', $m[1] . '.' . $m[2], secret_key());
    if (!hash_equals($expected, $m[3]) || (int) $m[2] < time()) {
        return null;
    }
    return (int) $m[1];
}

// ---------------------------------------------------------------------------
// Manifest (public case list + map geometry)
// ---------------------------------------------------------------------------
function manifest(): array
{
    static $m = null;
    if ($m === null) {
        $p = DANA_CASES . '/manifest.json';
        if (!is_file($p)) {
            fail(503, 'No cases deployed yet');
        }
        $m = json_decode((string) file_get_contents($p), true, 512, JSON_THROW_ON_ERROR);
        sync_cases_if_needed(db(), $m, $p);
    }
    return $m;
}
