#!/usr/bin/env bash
# Daily filament-kit backup (run by filament-kit-backup.service as root).
# Writes /var/backups/filament-kit/<date>.tar.gz with a consistent copy of the
# Spoolman database, /etc/filament-kit and /var/lib/filament-kit; keeps backup.keep_days days (default 14).
# The setup page offers each as the same zip `filament-kit backup create` writes (filament_kit/backup.py).
set -euo pipefail
umask 077

DEST=/var/backups/filament-kit
SPOOLMAN_DB=/var/lib/spoolman/spoolman.db
PYTHON=/opt/filament-kit/venv/bin/python

mkdir -p "$DEST"
# root only: a backup holds the setup code and the TLS key; the setup page gets one through the root helper
chmod 700 "$DEST"
KEEP=$("$PYTHON" -I -m filament_kit.backup keep-days 2>/dev/null) || KEEP=14
case "$KEEP" in ''|*[!0-9]*) KEEP=14 ;; esac

work=$(mktemp -d)
failed_reported=0
on_err() {
    if [ "$failed_reported" -eq 0 ]; then
        failed_reported=1
        "$PYTHON" -I -m filament_kit.backup event-failed "nightly backup failed" true 2>/dev/null || true
    fi
}
trap 'on_err' ERR
trap 'rm -rf "$work"' EXIT

if [ -f "$SPOOLMAN_DB" ]; then
    # sqlite3 online backup: safe while Spoolman is writing.
    "$PYTHON" -I - "$SPOOLMAN_DB" "$work/spoolman.db" <<'EOF'
import sqlite3
import sys

src = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
dst = sqlite3.connect(sys.argv[2])
with dst:
    src.backup(dst)
dst.close()
src.close()
EOF
else
    echo "note: $SPOOLMAN_DB not found, backing up settings and state only" >&2
fi

paths=()
for p in etc/filament-kit var/lib/filament-kit var/lib/filament-kit-web; do
    if [ -e "/$p" ]; then
        paths+=("$p")
    fi
done

base="$DEST/$(date +%F).tar.gz"
tmp=$(mktemp "$DEST/.nightly-XXXXXXXX.tar.gz.tmp")
trap 'rm -f -- "$tmp" "$tmp.enc"; rm -rf -- "$work"' EXIT
# Skipped: data that is downloaded again (the uploaded Bambu system presets, earlier library versions),
# half-written builds, and the backup password itself.
# --acls/--xattrs: the web unit's grants (filament_kit/web_access.py) come back with a restore
tar -czf "$tmp" --acls --xattrs \
    --exclude='etc/filament-kit/backup_password' \
    --exclude='var/lib/filament-kit/profiles/live/system' \
    --exclude='var/lib/filament-kit/library/versions' \
    --exclude='var/lib/filament-kit/profiles/builds/*.tmp' \
    -C "$work" . -C / "${paths[@]}"

rc=0
"$PYTHON" -I -m filament_kit.backup encrypt "$tmp" "$tmp.enc" || rc=$?
if [ "$rc" -eq 0 ]; then
    rm -f "$tmp"
    out="$base.enc"
    mv -f "$tmp.enc" "$out"
    encrypted=true
elif [ "$rc" -eq 2 ]; then
    out="$base"
    mv -f "$tmp" "$out"
    encrypted=false
else
    rm -f "$tmp" "$tmp.enc"
    failed_reported=1
    "$PYTHON" -I -m filament_kit.backup event-failed "encryption failed" true 2>/dev/null || true
    exit 1
fi

# Keep backup.keep_days days (14 unless set on the setup page): the nightly ones, the page's and a restore's own.
if ! find "$DEST" -maxdepth 1 -type f \( -name '*.tar.gz' -o -name '*.tar.gz.enc' -o -name 'filament-kit-backup-*.zip' -o -name 'filament-kit-backup-*.zip.enc' -o -name 'pre-restore-*.zip' -o -name 'pre-restore-*.zip.enc' \) \
    -mtime +$((KEEP - 1)) -delete; then
    echo "warning: backup retention could not finish" >&2
fi
# Reclaim interrupted nightly work, without touching another run's active files.
if ! find "$DEST" -maxdepth 1 -type f \( -name '.nightly-*.tar.gz.tmp' -o -name '.nightly-*.tar.gz.tmp.enc' -o -name '????-??-??.tar.gz.tmp' -o -name '????-??-??.tar.gz.tmp.enc' \) \
    -mmin +1440 -delete; then
    echo "warning: could not remove interrupted backup files" >&2
fi

size=$(stat -c %s "$out" 2>/dev/null || wc -c < "$out")
"$PYTHON" -I -m filament_kit.backup event-finished "$(basename "$out")" "$size" "$encrypted" true 2>/dev/null || true

echo "backup written: $out"
