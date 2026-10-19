#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib-qa.sh"
qa_acquire_lock

DEV_FILES_ROOT="${QA_DEV_FILES_ROOT:-/root/projects/gba-server/src/Global.Business.Assistant.Api}"

"$SCRIPT_DIR/qa-clone.sh"

for folder in Data Images; do
  mkdir -p "$QA_STATE_DIR/concord/$folder"
  rsync -a --delete "$DEV_FILES_ROOT/$folder/" "$QA_STATE_DIR/concord/$folder/"
done

"$SCRIPT_DIR/qa-up.sh"
echo "QA stand refreshed from DEV."
