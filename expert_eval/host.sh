#!/usr/bin/env bash
# host.sh — deploy and manage the DANA expert site on the group web host (PHP + SQLite).
#
#   ./host.sh deploy                 copy code, cases and maps; create/upgrade the database
#   ./host.sh admin add-codes --n 5 --label AEMET
#   ./host.sh admin list-codes | stats
#   ./host.sh pull-db [file]         consistent copy of the answers to this machine (for admin.py)
#   ./host.sh set-smtp               store the SMTP login for code emails on the host (prompts)
#   ./host.sh test-email ADDRESS     send a test email with the configured transport
#   ./host.sh archive LABEL          new campaign: keep the answers as LABEL, start an empty database
#
# Run from anywhere; paths are relative to this script. Override the target with
# DANA_HOST, DANA_SSH_KEY, DANA_SITE_ROOT, DANA_URL_PATH.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOST="${DANA_HOST:-meteorologia@meteorologia.uib.es}"
KEY="${DANA_SSH_KEY:-$HOME/.ssh/meteo}"
SITE="${DANA_SITE_ROOT:-/srv/www/meteorologia.uib.es}"
URL_PATH="${DANA_URL_PATH:-dana}"
PUBLIC="$SITE/web/$URL_PATH"
APP="$SITE/dana_app"
DATA="$SITE/dana_data"
PY="${PYTHON:-python3}"

# one shared SSH connection for all steps (the host limits rapid new connections)
MUX=(-o ControlMaster=auto -o "ControlPath=$HOME/.ssh/cm-dana-%r@%h:%p" -o ControlPersist=120)
SSH=(ssh -o BatchMode=yes -i "$KEY" "${MUX[@]}")
RSYNC=(rsync -rltp --chmod=Du=rwx,Dg=rx,Do=,Fu=rw,Fg=r,Fo= -e "ssh -o BatchMode=yes -i $KEY ${MUX[*]}")

remote() { "${SSH[@]}" "$HOST" "$@"; }

cmd_deploy() {
  [[ -f "$HERE/cases/manifest.json" ]] || { echo "No cases/manifest.json: run build_cases.py first" >&2; exit 1; }
  TMP_DEPLOY="$(mktemp -d)"; trap 'rm -rf "$TMP_DEPLOY"' EXIT
  local tmp="$TMP_DEPLOY"
  (cd "$HERE" && "$PY" settings.py > "$tmp/settings.json")

  echo "== folders"
  remote "mkdir -p '$PUBLIC/frames' '$APP/cases' '$APP/dev' '$DATA' && chmod 700 '$APP' '$DATA'"

  echo "== server code -> $APP"
  "${RSYNC[@]}" --delete --exclude cases/ --exclude dev/ \
    "$HERE/php/app/" "$HERE/schema.sql" "$HERE/email_code.txt" "$HERE/email_request.html" "$HERE/email_reply.html" "$tmp/settings.json" "$HOST:$APP/"
  "${RSYNC[@]}" --delete "$HERE/php/devtool.php" "$HERE/php/dev_router.php" "$HOST:$APP/dev/"

  echo "== case data (manifest, evaluation files, overlays) -> $APP/cases"
  "${RSYNC[@]}" --delete --include manifest.json --include 'eval/' --include 'eval/*.json' \
    --include 'overlays/' --include 'overlays/*.png' --exclude '*' "$HERE/cases/" "$HOST:$APP/cases/"

  echo "== web page and maps -> $PUBLIC"
  "${RSYNC[@]}" --delete --exclude frames/ \
    "$HERE/static/index.html" "$HERE/php/public/api.php" "$HERE/php/public/.htaccess" "$HOST:$PUBLIC/"
  "${RSYNC[@]}" --delete "$HERE/static/app.js" "$HERE/static/style.css" "$HOST:$PUBLIC/static/"
  "${RSYNC[@]}" --delete "$HERE/cases/frames/" "$HOST:$PUBLIC/frames/"
  # the rsync --chmod above keeps everything private to the account; Apache runs as the same user

  echo "== database"
  remote "cd '$APP' && php admin.php init"
  echo "Deployed: https://${HOST#*@}/$URL_PATH/"
}

cmd_admin() { remote "cd '$APP' && php admin.php $(printf '%q ' "$@")"; }

cmd_pull_db() {
  local dest="${1:-$HERE/data/responses_host.sqlite}" snap="$DATA/snapshot-$$.sqlite"
  mkdir -p "$(dirname "$dest")"
  remote "cd '$APP' && php admin.php snapshot '$snap' >/dev/null"
  "${RSYNC[@]}" "$HOST:$snap" "$dest"
  remote "rm -f '$snap'"
  echo "Copied answers to $dest"
  echo "Analyse with: EXPERT_EVAL_DB=$dest python admin.py report"
}

cmd_set_smtp() {
  local user pass
  read -rp "SMTP login / sender address: " user
  read -rsp "Password, app password or SMTP token (hidden): " pass; echo
  pass="${pass// /}"   # Google shows app passwords in groups of four
  [[ -n "$user" && -n "$pass" ]] || { echo "Nothing stored" >&2; exit 1; }
  # passed on stdin only (never as command-line arguments visible to other users)
  printf '%s\n%s\n' "$user" "$pass" \
    | "$PY" -c 'import json,sys; u,p=sys.stdin.read().split("\n")[:2]; print(json.dumps({"username": u, "password": p}))' \
    | remote "umask 077 && mkdir -p '$DATA' && cat > '$DATA/smtp_credentials.json'"
  echo "Stored on the host in $DATA/smtp_credentials.json (readable only by the site account)."
  echo "Check it with: ./host.sh test-email your@address"
}

cmd_test_email() { remote "cd '$APP' && php admin.php test-email $(printf '%q' "${1:?usage: ./host.sh test-email ADDRESS}")"; }

cmd_archive() {
  local label="${1:?usage: ./host.sh archive LABEL   (e.g. campaign1)}"
  [[ "$label" =~ ^[A-Za-z0-9._-]+$ ]] || { echo "LABEL may only contain letters, digits, . _ -" >&2; exit 1; }
  echo "This keeps a copy of all answers in data/archive/responses_$label.sqlite, renames the host's"
  echo "dana_data to dana_data_$label and starts an EMPTY database (codes and answers start from zero)."
  read -rp "Type the label to confirm: " ok
  [[ "$ok" == "$label" ]] || { echo "Cancelled"; exit 1; }
  mkdir -p "$HERE/data/archive"
  cmd_pull_db "$HERE/data/archive/responses_$label.sqlite"
  remote "set -e; cd '$SITE'; if [ -e 'dana_data_$label' ]; then echo 'dana_data_$label already exists' >&2; exit 1; fi
          mv dana_data 'dana_data_$label'; mkdir -m 700 dana_data
          for f in secret_key flow_key smtp_credentials.json; do [ -f \"dana_data_$label/\$f\" ] && cp -p \"dana_data_$label/\$f\" dana_data/; done
          cd '$APP' && php admin.php init"
  echo "Archived on the host as $SITE/dana_data_$label; the site now uses an empty database."
}

case "${1:-}" in
  deploy) shift; cmd_deploy "$@" ;;
  admin) shift; cmd_admin "$@" ;;
  pull-db) shift; cmd_pull_db "$@" ;;
  set-smtp) shift; cmd_set_smtp "$@" ;;
  test-email) shift; cmd_test_email "$@" ;;
  archive) shift; cmd_archive "$@" ;;
  *) sed -n '2,14p' "$0"; exit 2 ;;
esac
