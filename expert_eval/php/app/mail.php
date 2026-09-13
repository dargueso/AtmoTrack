<?php
/**
 * mail.php — email the personal code to an expert who requested one.
 *
 * settings.json [email] transport:
 *   "smtp"  authenticated SMTP with a dedicated Gmail account (app password). Credentials
 *           live only in <data>/smtp_credentials.json {"username": ..., "password": ...}.
 *   "mail"  PHP mail() through the web host's relay (iCloud and others reject the UIB relay IP).
 * settings.json [email] mode: "notify" emails each request to notify_address (you send the code),
 * "send" emails the code to the expert.
 * Tests: set DANA_MAIL_OUTBOX to a folder and messages are written there instead of sent.
 */

declare(strict_types=1);

function encode_header(string $text): string
{
    return preg_match('/[^\x20-\x7e]/', $text) ? '=?UTF-8?B?' . base64_encode($text) . '?=' : $text;
}

function display_address(string $name, string $address): string
{
    $n = preg_match('/[^\x20-\x7e]/', $name) ? encode_header($name) : '"' . addcslashes($name, "\"\\") . '"';
    return "$n <$address>";
}

function smtp_credentials(): ?array
{
    $p = getenv('DANA_SMTP_CREDENTIALS') ?: DANA_DATA . '/smtp_credentials.json';
    if (!is_file($p)) {
        return null;
    }
    $c = json_decode((string) file_get_contents($p), true);
    return is_array($c) && isset($c['username'], $c['password']) ? $c : null;
}

/** Sender address: settings from_address, or the SMTP login when left empty. */
function sender_address(array $cfg): string
{
    if (!empty($cfg['from_address'])) {
        return $cfg['from_address'];
    }
    return smtp_credentials()['username'] ?? '';
}

/** Send a plain-text UTF-8 email. Throws RuntimeException with the reason on failure. */
function send_mail(string $to, string $subject, string $body, ?string $replyTo = null, bool $html = false): void
{
    $cfg = settings()['email'];
    $from = sender_address($cfg);
    if ($from === '') {
        if (DANA_MAIL_OUTBOX === '') {
            throw new RuntimeException('no sender address (set [email] from_address or run ./host.sh set-smtp)');
        }
        $from = 'site@localhost';  // tests writing to the outbox need no real sender
    }
    $replyTo = $replyTo ?: (($cfg['reply_to'] ?? '') ?: $from);
    $domain = substr((string) strrchr($from, '@'), 1) ?: 'localhost';
    $headers = [
        'Date: ' . gmdate('D, d M Y H:i:s') . ' +0000',
        'From: ' . display_address($cfg['from_name'], $from),
        "Reply-To: $replyTo",
        'Message-ID: <' . bin2hex(random_bytes(12)) . "@$domain>",
        'MIME-Version: 1.0',
        'Content-Type: text/' . ($html ? 'html' : 'plain') . '; charset=UTF-8',
        'Content-Transfer-Encoding: base64',
    ];
    $encodedBody = chunk_split(base64_encode(str_replace("\n", "\r\n", str_replace("\r\n", "\n", $body))));

    if (DANA_MAIL_OUTBOX !== '') {
        if (!is_dir(DANA_MAIL_OUTBOX)) {
            mkdir(DANA_MAIL_OUTBOX, 0700, true);
        }
        $file = sprintf('%s/%.6f-%s.eml', DANA_MAIL_OUTBOX, microtime(true), bin2hex(random_bytes(3)));
        $msg = "To: $to\r\nSubject: " . encode_header($subject) . "\r\n" . implode("\r\n", $headers) . "\r\n\r\n" . $encodedBody;
        if (file_put_contents($file, $msg) === false) {
            throw new RuntimeException('cannot write outbox');
        }
        return;
    }

    if (($cfg['transport'] ?? 'mail') === 'mail') {
        if (!mail($to, encode_header($subject), $encodedBody, implode("\r\n", $headers), '-f' . $from)) {
            throw new RuntimeException('mail() returned false');
        }
        return;
    }

    $cred = smtp_credentials();
    if ($cred === null) {
        throw new RuntimeException('SMTP credentials missing (run ./host.sh set-smtp)');
    }
    $message = implode("\r\n", array_merge(["To: $to", 'Subject: ' . encode_header($subject)], $headers))
        . "\r\n\r\n" . $encodedBody;
    smtp_send($cfg, $cred, $from, $to, $message);
}

/** Minimal SMTP client: TLS (STARTTLS or implicit), AUTH LOGIN, one recipient. */
function smtp_send(array $cfg, array $cred, string $from, string $to, string $message): void
{
    $host = $cfg['smtp_host'];
    $port = (int) $cfg['smtp_port'];
    $security = $cfg['smtp_security'] ?? 'starttls';
    $ctx = stream_context_create(['ssl' => ['verify_peer' => true, 'verify_peer_name' => true, 'peer_name' => $host]]);
    $scheme = $security === 'ssl' ? 'ssl' : 'tcp';
    $fp = @stream_socket_client("$scheme://$host:$port", $errno, $errstr, 20, STREAM_CLIENT_CONNECT, $ctx);
    if (!$fp) {
        throw new RuntimeException("SMTP connect to $host:$port failed: $errstr");
    }
    stream_set_timeout($fp, 30);

    $read = function () use ($fp): array {
        $lines = [];
        while (($line = fgets($fp, 2048)) !== false) {
            $lines[] = rtrim($line, "\r\n");
            if (strlen($line) < 4 || $line[3] !== '-') {
                break;
            }
        }
        $last = end($lines) ?: '';
        return [(int) substr($last, 0, 3), implode(' | ', $lines)];
    };
    $cmd = function (?string $line, array $expect, string $what) use ($fp, $read): string {
        if ($line !== null) {
            fwrite($fp, $line . "\r\n");
        }
        [$code, $text] = $read();
        if (!in_array($code, $expect, true)) {
            throw new RuntimeException("SMTP $what failed: $text");
        }
        return $text;
    };

    try {
        $cmd(null, [220], 'greeting');
        $helo = 'EHLO ' . (gethostname() ?: 'localhost');
        $cmd($helo, [250], 'EHLO');
        if ($security === 'starttls') {
            $cmd('STARTTLS', [220], 'STARTTLS');
            $methods = STREAM_CRYPTO_METHOD_TLSv1_2_CLIENT
                | (defined('STREAM_CRYPTO_METHOD_TLSv1_3_CLIENT') ? STREAM_CRYPTO_METHOD_TLSv1_3_CLIENT : 0);
            if (!stream_socket_enable_crypto($fp, true, $methods)) {
                throw new RuntimeException('SMTP TLS handshake failed');
            }
            $cmd($helo, [250], 'EHLO after STARTTLS');
        }
        $cmd('AUTH LOGIN', [334], 'AUTH');
        $cmd(base64_encode($cred['username']), [334], 'AUTH user');
        $cmd(base64_encode($cred['password']), [235], 'AUTH password');
        $cmd("MAIL FROM:<$from>", [250], 'MAIL FROM');
        $cmd("RCPT TO:<$to>", [250, 251], 'RCPT TO');
        $cmd('DATA', [354], 'DATA');
        $data = preg_replace('/^\./m', '..', $message);  // dot-stuffing
        $cmd($data . "\r\n.", [250], 'message');
        fwrite($fp, "QUIT\r\n");
    } finally {
        fclose($fp);
    }
}

function send_code_email(string $to, string $name, string $code): void
{
    $cfg = settings()['email'];
    $body = strtr((string) file_get_contents(DANA_EMAIL_TEMPLATE), [
        '{name}' => $name,
        '{code}' => $code,
        '{site_url}' => $cfg['site_url'],
        '{from_name}' => $cfg['from_name'],
    ]);
    send_mail($to, 'Your DANA Expert Check code', $body);
}

/** Secret included in request emails so the Power Automate flow only acts on genuine ones. */
function flow_key(): string
{
    $p = DANA_DATA . '/flow_key';
    if (!is_file($p)) {
        ensure_data_dir();
        file_put_contents($p, bin2hex(random_bytes(16)), LOCK_EX);
        chmod($p, 0600);
    }
    return trim((string) file_get_contents($p));
}

/**
 * mode "notify": email the request to the organisers (HTML). Reply-To is the requester, and an
 * encoded block lets a Power Automate flow send the ready-made reply automatically (see README).
 */
function send_request_notification(string $email, string $name, string $affiliation, string $experience,
                                   string $code, int $nSent): void
{
    $cfg = settings()['email'];
    $dir = getenv('DANA_TEMPLATE_DIR') ?: dirname(DANA_EMAIL_TEMPLATE);
    $h = fn(string $v): string => htmlspecialchars($v, ENT_QUOTES, 'UTF-8');
    $reply = strtr((string) file_get_contents("$dir/email_reply.html"), [
        '{name}' => $h($name), '{code}' => $h($code), '{site_url}' => $h($cfg['site_url']),
    ]);
    $body = strtr((string) file_get_contents("$dir/email_request.html"), [
        '{repeat_note}' => $nSent > 1 ? $h(" (repeat request #$nSent: same code as before)") : '',
        '{name}' => $h($name),
        '{affiliation}' => $h($affiliation),
        '{experience}' => $h($experience),
        '{email}' => $h($email),
        '{code}' => $h($code),
        '{reply_html}' => $reply,
        '{flow_key}' => flow_key(),
        '{to_b64}' => base64_encode($email),
        '{body_b64}' => base64_encode($reply),
    ]);
    $subject = 'DANA Expert Check: code request from ' . $name . ' (' . $affiliation . ')' . ($nSent > 1 ? ' [repeat]' : '');
    send_mail($cfg['notify_address'], $subject, $body, $email, true);
}
