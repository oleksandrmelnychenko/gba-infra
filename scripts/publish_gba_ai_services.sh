#!/usr/bin/env bash
# Merge-safe synchronization for the seven standalone GBA AI services.
#
# The existing gba-ai-services checkout remains the source of Git history and
# remote configuration. The default mode is a read-only preview. A real sync,
# commit, push, or reverse sync to the host-run service copies is always explicit.
set -euo pipefail

ROOT="${GBA_PROJECTS_ROOT:-/root/projects}"
MONOREPO="${GBA_AI_SERVICES_DEST:-$ROOT/gba-ai-services}"
SERVICES=(
  gba-nba
  gba-reco
  gba-procure
  gba-solvency
  gba-pricing
  gba-products
  gba-forecast
)

DIRECTION="to-monorepo"
APPLY=0
COMMIT=0
PUSH=0
COMMIT_MESSAGE="chore(ai-fleet): sync standalone service sources"

usage() {
  cat <<'EOF'
Usage: publish_gba_ai_services.sh [OPTIONS]

Without options the command previews standalone -> monorepo changes.

Direction:
  --to-monorepo      Standalone service repos -> gba-ai-services (default).
  --to-standalone    Combined gba-ai-services trees -> host-run standalone repos.

Actions:
  --dry-run          Preview service-scoped changes (default).
  --apply            Apply the reviewed service-scoped rsync.
  --commit           Commit monorepo service paths after --apply.
  --push             Push the monorepo branch normally after --apply --commit.
  --message TEXT     Commit message used with --commit.
  -h, --help         Show this help.

Reverse sync intentionally does not commit or push standalone repositories. It
requires the monorepo and every standalone destination to be clean before apply.

Environment overrides used by tests or alternate checkouts:
  GBA_PROJECTS_ROOT
  GBA_AI_SERVICES_DEST
EOF
}

die() {
  echo "ABORT: $*" >&2
  exit 1
}

while (($#)); do
  case "$1" in
    --to-monorepo)
      DIRECTION="to-monorepo"
      ;;
    --to-standalone)
      DIRECTION="to-standalone"
      ;;
    --dry-run)
      APPLY=0
      ;;
    --apply)
      APPLY=1
      ;;
    --commit)
      COMMIT=1
      ;;
    --push)
      PUSH=1
      ;;
    --message)
      shift
      (($#)) || die "--message requires a value"
      COMMIT_MESSAGE="$1"
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown option: $1"
      ;;
  esac
  shift
done

((COMMIT == 0 || APPLY == 1)) || die "--commit requires --apply"
((PUSH == 0 || (APPLY == 1 && COMMIT == 1))) \
  || die "--push requires --apply --commit"
[[ -n "$COMMIT_MESSAGE" ]] || die "commit message must not be empty"
if [[ "$DIRECTION" == "to-standalone" ]] && ((COMMIT == 1 || PUSH == 1)); then
  die "--commit and --push are available only with --to-monorepo"
fi

for command_name in git rsync grep find realpath mktemp mkdir rm; do
  command -v "$command_name" >/dev/null \
    || die "required command is unavailable: $command_name"
done

[[ -d "$MONOREPO" ]] || die "monorepo checkout does not exist: $MONOREPO"
[[ ! -L "$MONOREPO" ]] || die "monorepo checkout must not be a symlink"
MONOREPO_REAL="$(realpath "$MONOREPO")"
TOP_LEVEL="$(git -C "$MONOREPO" rev-parse --show-toplevel 2>/dev/null)" \
  || die "monorepo is not an existing Git worktree"
[[ "$(realpath "$TOP_LEVEL")" == "$MONOREPO_REAL" ]] \
  || die "monorepo path must be the root of its existing Git worktree"

for service in "${SERVICES[@]}"; do
  standalone="$ROOT/$service"
  monorepo_service="$MONOREPO/$service"
  [[ -d "$standalone" ]] || die "standalone service is missing: $standalone"
  [[ -d "$monorepo_service" ]] || die "monorepo service is missing: $monorepo_service"
  [[ ! -L "$standalone" ]] || die "standalone service must not be a symlink: $standalone"
  [[ ! -L "$monorepo_service" ]] \
    || die "monorepo service must not be a symlink: $monorepo_service"
  standalone_top="$(git -C "$standalone" rev-parse --show-toplevel 2>/dev/null)" \
    || die "standalone service is not a Git worktree: $standalone"
  [[ "$(realpath "$standalone_top")" == "$(realpath "$standalone")" ]] \
    || die "standalone path must be its Git worktree root: $standalone"
  case "$(realpath "$monorepo_service")/" in
    "$MONOREPO_REAL"/*/) ;;
    *) die "service path escapes the monorepo: $monorepo_service" ;;
  esac
done

monorepo_dirty="$(git -C "$MONOREPO" status --porcelain=v1)"
if [[ "$DIRECTION" == "to-monorepo" ]]; then
  if ((APPLY == 1)) && [[ -n "$monorepo_dirty" ]]; then
    die "monorepo is dirty; commit/stash or merge changes explicitly before --apply"
  fi
  if ((APPLY == 0)) && [[ -n "$monorepo_dirty" ]]; then
    echo ">> monorepo is dirty; preview only (no files will be changed)"
  fi
else
  if ((APPLY == 1)) && [[ -n "$monorepo_dirty" ]]; then
    die "combined monorepo source must be clean before reverse sync"
  fi
  for service in "${SERVICES[@]}"; do
    standalone_dirty="$(git -C "$ROOT/$service" status --porcelain=v1)"
    if ((APPLY == 1)) && [[ -n "$standalone_dirty" ]]; then
      die "standalone destination is dirty: $ROOT/$service"
    fi
    if ((APPLY == 0)) && [[ -n "$standalone_dirty" ]]; then
      echo ">> $service is dirty; reverse preview only"
    fi
  done
fi

CREDENTIAL_PATTERN='(Grimm_jow92|78\.152\.175\.67|ef_migrator|_dev_internal_[0-9a-f]+|Ro_2026_dev|NbaRo_2026|Nba_dev_2026|-----BEGIN ([A-Z0-9 ]+ )?PRIVATE KEY-----|AKIA[0-9A-Z]{16}|github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9]{20,})'

scan_credentials() {
  local scan_root="$1"
  local file
  local -a matches=()
  while IFS= read -r -d '' file; do
    if LC_ALL=C grep -IlEq "$CREDENTIAL_PATTERN" "$file"; then
      matches+=("$file")
    fi
  done < <(find "$scan_root" -type f -print0)
  if ((${#matches[@]})); then
    printf 'Credential-like value found in:\n' >&2
    printf '  %s\n' "${matches[@]}" >&2
    return 1
  fi
}

STAGING_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/gba-ai-sync.XXXXXX")"
trap 'rm -rf -- "$STAGING_ROOT"' EXIT
declare -A STAGED_DIRS=()

stage_publishable_tree() {
  local source_dir="$1"
  local stage_dir="$2"
  local file_list="$3"
  local path

  mkdir -p "$stage_dir"
  : > "$file_list"
  while IFS= read -r -d '' path; do
    [[ "$path" != /* && "$path" != ../* && "$path" != */../* ]] \
      || die "unsafe Git path in $source_dir: $path"
    case "/$path" in
      */.env.example) ;;
      */.env|*/.env.*) die "non-template env file is publishable in Git: $source_dir/$path" ;;
    esac
    [[ ! -L "$source_dir/$path" ]] \
      || die "publishable symlinks are not allowed: $source_dir/$path"
    if [[ -f "$source_dir/$path" ]]; then
      printf '%s\0' "$path" >> "$file_list"
    elif [[ -e "$source_dir/$path" ]]; then
      die "publishable path is not a regular file: $source_dir/$path"
    fi
  done < <(
    git -C "$source_dir" ls-files -z --cached --others --exclude-standard
  )

  rsync -a --from0 --files-from="$file_list" "$source_dir/" "$stage_dir/"
  scan_credentials "$stage_dir" \
    || die "credential scan failed before sync"
}

echo ">> staging Git-tracked and non-ignored service content"
for service in "${SERVICES[@]}"; do
  if [[ "$DIRECTION" == "to-monorepo" ]]; then
    source_dir="$ROOT/$service"
  else
    source_dir="$MONOREPO/$service"
  fi
  stage_dir="$STAGING_ROOT/$service"
  file_list="$STAGING_ROOT/$service.files"
  stage_publishable_tree "$source_dir" "$stage_dir" "$file_list"
  STAGED_DIRS["$service"]="$stage_dir"
done

RSYNC_ARGS=(
  -a
  --checksum
  --delete
  --delete-delay
  --itemize-changes
  --exclude=.git/
  --exclude=.venv/
  --exclude=venv/
  --exclude=__pycache__/
  --exclude=.ruff_cache/
  --exclude=.pytest_cache/
  --exclude=.mypy_cache/
  --exclude=node_modules/
  # Preserve only the runtime data directory at the service root.  An
  # unanchored ``data/`` pattern also matches publishable source such as
  # ``app/data/*.py`` and would silently omit database repositories.
  --exclude=/data/
  --exclude='backup_*/'
  --exclude='*.egg-info/'
  --exclude='*.py[cod]'
  --exclude='*.log'
  --exclude='*.rdb'
  --exclude='*.pem'
  --exclude=.DS_Store
  --exclude=.vscode/
  --exclude=.idea/
  --exclude='benchmarks/*.json'
  --exclude='app/risk/artifacts/*.joblib'
  --exclude='app/risk/artifacts/*.pkl'
  --exclude='app/risk/artifacts/*.png'
  --include=.env.example
  --exclude='.env*'
)
if ((APPLY == 0)); then
  RSYNC_ARGS+=(--dry-run)
  echo ">> DRY RUN: $DIRECTION service-scoped rsync preview"
else
  echo ">> applying $DIRECTION service-scoped rsync"
fi

for service in "${SERVICES[@]}"; do
  if [[ "$DIRECTION" == "to-monorepo" ]]; then
    destination_dir="$MONOREPO/$service"
  else
    destination_dir="$ROOT/$service"
  fi
  echo ">> $service"
  rsync "${RSYNC_ARGS[@]}" "${STAGED_DIRS[$service]}/" "$destination_dir/"
done

if ((APPLY == 0)); then
  echo ">> DRY RUN complete; review every change before using --apply"
  exit 0
fi

if [[ "$DIRECTION" == "to-standalone" ]]; then
  echo ">> reverse sync complete; standalone worktrees now contain the combined service trees"
  for service in "${SERVICES[@]}"; do
    git -C "$ROOT/$service" status --short
  done
  exit 0
fi

git -C "$MONOREPO" status --short -- "${SERVICES[@]}"

if ((COMMIT == 1)); then
  git -C "$MONOREPO" add -A -- "${SERVICES[@]}"
  if git -C "$MONOREPO" diff --cached --quiet -- "${SERVICES[@]}"; then
    echo ">> no service changes to commit"
  else
    git -C "$MONOREPO" commit -m "$COMMIT_MESSAGE"
  fi
fi

if ((PUSH == 1)); then
  [[ -z "$(git -C "$MONOREPO" status --porcelain=v1)" ]] \
    || die "monorepo has uncommitted changes; refusing to push"
  branch="$(git -C "$MONOREPO" branch --show-current)"
  [[ -n "$branch" ]] || die "cannot push a detached HEAD"
  git -C "$MONOREPO" remote get-url origin >/dev/null \
    || die "monorepo has no origin remote"
  echo ">> pushing $branch to origin with normal fast-forward checks"
  git -C "$MONOREPO" push origin "$branch"
fi

echo ">> done"
