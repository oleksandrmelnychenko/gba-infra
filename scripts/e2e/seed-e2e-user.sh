#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib-e2e-sql.sh"
e2e_acquire_stand_lock

E2E_LOGIN="${E2E_LOGIN:-admin.local}"
E2E_EMAIL="${E2E_EMAIL:-admin.local@gba.test}"
ENV_FILE="${ENV_FILE:-/etc/gba-e2e.env}"

e2e_require_sql_container

password="E2e!$(openssl rand -hex 12)"

hash="$(python3 - "$password" <<'PY'
import sys, os, base64, hashlib, struct
pwd = sys.argv[1].encode()
salt = os.urandom(16)
subkey = hashlib.pbkdf2_hmac('sha256', pwd, salt, 10000, 32)
blob = bytes([1]) + struct.pack('>III', 1, 10000, 16) + salt + subkey
print(base64.b64encode(blob).decode())
PY
)"

e2e_sql <<SQL
SET NOCOUNT ON;
SET QUOTED_IDENTIFIER ON;
SET ANSI_NULLS ON;
$(e2e_fence_sql)
$(e2e_db_fence_sql ConcordIdentityDb_E2E)
UPDATE [ConcordIdentityDb_E2E].dbo.AspNetUsers
SET PasswordHash = N'$hash', LockoutEnd = NULL, AccessFailedCount = 0
WHERE UserName = N'$E2E_LOGIN' AND Email = N'$E2E_EMAIL';
IF @@ROWCOUNT <> 1 THROW 54830, N'Expected to update exactly one $E2E_LOGIN row in ConcordIdentityDb_E2E.', 1;
SQL

umask 077
printf 'E2E_USERNAME=%s\nE2E_PASSWORD=%s\n' "$E2E_LOGIN" "$password" > "$ENV_FILE"
chmod 600 "$ENV_FILE"

echo "Password of $E2E_LOGIN reset inside ConcordIdentityDb_E2E only; credentials written to $ENV_FILE"
