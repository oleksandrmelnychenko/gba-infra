#!/usr/bin/env bash
# Merge-safe synchronization between the standalone GBA AI service repositories
# and their copies in the gba-ai-services monorepo.
#
# Only committed content (HEAD) of the source is published. The default mode is
# a read-only preview. gba-products is never touched unless --include-products
# is given: the monorepo tree is the live source of that service.
set -euo pipefail

ROOT="${GBA_PROJECTS_ROOT:-/root/projects}"
MONOREPO="${GBA_AI_SERVICES_DEST:-$ROOT/gba-ai-services}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ALLOWLIST_FILE="${GBA_AI_PUBLISH_ALLOWLIST:-$SCRIPT_DIR/publish_dirty_allowlist.txt}"
STANDALONE_SERVICES=(
  gba-nba
  gba-reco
  gba-procure
  gba-solvency
  gba-pricing
  gba-forecast
)
MONOREPO_OWNED_SERVICE="gba-products"

DIRECTION="to-monorepo"
APPLY=0
COMMIT=0
PUSH=0
INCLUDE_PRODUCTS=0
ONLY=""
COMMIT_MESSAGE="chore(ai-fleet): sync standalone service sources"

usage() {
  cat <<'USAGE'
Usage: publish_gba_ai_services.sh [OPTIONS]

Without options the command previews standalone -> monorepo changes.

Direction:
  --to-monorepo        Standalone service repos -> gba-ai-services (default).
  --to-standalone      gba-ai-services trees -> host-run standalone repos.

Scope:
  --only A[,B]         Limit the run to the named services.
  --include-products   Also sync gba-products. Off by default in both
                       directions: the monorepo tree is the source that runs.

Actions:
  --dry-run            Preview service-scoped changes (default).
  --apply              Apply the reviewed service-scoped rsync.
  --commit             Commit monorepo service paths after --apply.
  --push               Push the monorepo branch normally after --apply --commit.
  --message TEXT       Commit message used with --commit.
  -h, --help           Show this help.

Only the committed HEAD of the source is copied. Ignored files (.venv*, .env,
caches, model artefacts) are never part of it and are never deleted on the
destination. A source whose tracked files are modified outside the allow-list
in publish_dirty_allowlist.txt is refused on --apply and flagged in a preview.
Allow-listed modifications are tolerated but not published.

Environment overrides used by tests or alternate checkouts:
  GBA_PROJECTS_ROOT
  GBA_AI_SERVICES_DEST
  GBA_AI_PUBLISH_ALLOWLIST
USAGE
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
    --include-products)
      INCLUDE_PRODUCTS=1
      ;;
    --only)
      shift
      (($#)) || die "--only requires a value"
      ONLY="$1"
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

for command_name in git rsync grep find realpath mktemp mkdir rm tar; do
  command -v "$command_name" >/dev/null \
    || die "required command is unavailable: $command_name"
done

CANDIDATES=("${STANDALONE_SERVICES[@]}")
if ((INCLUDE_PRODUCTS == 1)); then
  CANDIDATES+=("$MONOREPO_OWNED_SERVICE")
fi

SERVICES=()
if [[ -n "$ONLY" ]]; then
  IFS=',' read -r -a requested <<< "$ONLY"
  for name in "${requested[@]}"; do
    [[ -n "$name" ]] || die "--only contains an empty service name"
    if [[ "$name" == "$MONOREPO_OWNED_SERVICE" ]] && ((INCLUDE_PRODUCTS == 0)); then
      die "$MONOREPO_OWNED_SERVICE is synced only with --include-products"
    fi
    known=0
    for candidate in "${CANDIDATES[@]}"; do
      [[ "$candidate" == "$name" ]] && known=1
    done
    ((known == 1)) || die "unknown service in --only: $name"
    SERVICES+=("$name")
  done
else
  SERVICES=("${CANDIDATES[@]}")
fi

if ((INCLUDE_PRODUCTS == 0)); then
  echo ">> $MONOREPO_OWNED_SERVICE: skipped (monorepo is its source of truth; --include-products overrides)"
fi

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
  [[ ! -L "$standalone" ]] || die "standalone service must not be a symlink: $standalone"
  [[ ! -L "$monorepo_service" ]] \
    || die "monorepo service must not be a symlink: $monorepo_service"
  if [[ "$DIRECTION" == "to-standalone" && ! -d "$monorepo_service" ]]; then
    die "monorepo service is missing: $monorepo_service"
  fi
  standalone_top="$(git -C "$standalone" rev-parse --show-toplevel 2>/dev/null)" \
    || die "standalone service is not a Git worktree: $standalone"
  [[ "$(realpath "$standalone_top")" == "$(realpath "$standalone")" ]] \
    || die "standalone path must be its Git worktree root: $standalone"
  if [[ -d "$monorepo_service" ]]; then
    case "$(realpath "$monorepo_service")/" in
      "$MONOREPO_REAL"/*/) ;;
      *) die "service path escapes the monorepo: $monorepo_service" ;;
    esac
  fi
done

ALLOWLIST=()
if [[ -f "$ALLOWLIST_FILE" ]]; then
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%%#*}"
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line%"${line##*[![:space:]]}"}"
    [[ -n "$line" ]] && ALLOWLIST+=("$line")
  done < "$ALLOWLIST_FILE"
fi

is_allowlisted() {
  local path="$1"
  local pattern
  for pattern in "${ALLOWLIST[@]}"; do
    # shellcheck disable=SC2053
    [[ "$path" == $pattern ]] && return 0
  done
  return 1
}

BLOCKED_SERVICES=()

check_tracked_changes() {
  local repository="$1"
  local label="$2"
  local scope="${3:-}"
  local entry path
  local -a blocking=()
  local -a tolerated=()
  local -a status_args=(status --porcelain=v1 --untracked-files=no -z)
  if [[ -n "$scope" ]]; then
    status_args+=(-- "$scope")
  fi
  while IFS= read -r -d '' entry; do
    path="${entry:3}"
    if [[ "${entry:0:1}" == "R" || "${entry:0:1}" == "C" ]]; then
      IFS= read -r -d '' _ || true
    fi
    if [[ -n "$scope" ]]; then
      path="${path#"$scope"/}"
    fi
    if is_allowlisted "$path"; then
      tolerated+=("$path")
    else
      blocking+=("$path")
    fi
  done < <(git -C "$repository" "${status_args[@]}")
  if ((${#tolerated[@]})); then
    echo ">> $label: allow-listed local modifications are tolerated, HEAD is published: ${tolerated[*]}"
  fi
  if ((${#blocking[@]})); then
    echo ">> $label: REFUSED, tracked files modified outside the allow-list: ${blocking[*]}"
    BLOCKED_SERVICES+=("$label")
  fi
}

monorepo_dirty="$(git -C "$MONOREPO" status --porcelain=v1)"
if [[ "$DIRECTION" == "to-monorepo" ]]; then
  if ((APPLY == 1)) && [[ -n "$monorepo_dirty" ]]; then
    die "monorepo is dirty; commit/stash or merge changes explicitly before --apply"
  fi
  if ((APPLY == 0)) && [[ -n "$monorepo_dirty" ]]; then
    echo ">> monorepo is dirty; preview only (no files will be changed)"
  fi
  for service in "${SERVICES[@]}"; do
    check_tracked_changes "$ROOT/$service" "$service"
  done
  if ((APPLY == 1)) && ((${#BLOCKED_SERVICES[@]})); then
    die "standalone source is dirty: ${BLOCKED_SERVICES[*]}"
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

stage_committed_tree() {
  local repository="$1"
  local subtree="$2"
  local stage_dir="$3"
  local label="$4"
  local unpack_dir="$STAGING_ROOT/unpack-$label"
  local offending

  mkdir -p "$unpack_dir"
  if [[ -n "$subtree" ]]; then
    git -C "$repository" rev-parse --verify --quiet "HEAD:$subtree" >/dev/null \
      || die "nothing is committed for $label in $repository"
    git -C "$repository" archive --format=tar HEAD -- "$subtree" | tar -x -C "$unpack_dir"
    mv "$unpack_dir/$subtree" "$stage_dir"
  else
    git -C "$repository" archive --format=tar HEAD | tar -x -C "$unpack_dir"
    mv "$unpack_dir" "$stage_dir"
  fi

  offending="$(find "$stage_dir" -type l -print -quit)"
  [[ -z "$offending" ]] || die "publishable symlinks are not allowed: $label/${offending#"$stage_dir"/}"
  offending="$(find "$stage_dir" -type f -name '.env*' ! -name '.env.example' -print -quit)"
  [[ -z "$offending" ]] \
    || die "non-template env file is committed: $label/${offending#"$stage_dir"/}"
  offending="$(find "$stage_dir" -type d \( -name '.venv*' -o -name '__pycache__' \) -print -quit)"
  [[ -z "$offending" ]] \
    || die "virtualenv or cache directory is committed: $label/${offending#"$stage_dir"/}"
  scan_credentials "$stage_dir" \
    || die "credential scan failed before sync"
}

echo ">> staging committed (HEAD) service content"
for service in "${SERVICES[@]}"; do
  stage_dir="$STAGING_ROOT/$service"
  if [[ "$DIRECTION" == "to-monorepo" ]]; then
    stage_committed_tree "$ROOT/$service" "" "$stage_dir" "$service"
    untracked="$(git -C "$ROOT/$service" ls-files --others --exclude-standard | head -n 20)"
    if [[ -n "$untracked" ]]; then
      echo ">> $service: untracked files are not published: $(echo "$untracked" | tr '\n' ' ')"
    fi
  else
    stage_committed_tree "$MONOREPO" "$service" "$stage_dir" "$service"
  fi
  STAGED_DIRS["$service"]="$stage_dir"
done

RSYNC_ARGS=(
  -a
  --checksum
  --delete
  --delete-delay
  --itemize-changes
  --exclude=.git
  --exclude='.venv*/'
  --exclude=venv/
  --exclude=__pycache__/
  --exclude=.ruff_cache/
  --exclude=.pytest_cache/
  --exclude=.mypy_cache/
  --exclude=node_modules/
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
  --exclude='*.joblib'
  --exclude='*.pkl'
  --exclude='*.parquet'
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

SUMMARY=()
for service in "${SERVICES[@]}"; do
  if [[ "$DIRECTION" == "to-monorepo" ]]; then
    destination_dir="$MONOREPO/$service"
  else
    destination_dir="$ROOT/$service"
  fi
  echo ">> $service"
  note=""
  if [[ ! -d "$destination_dir" ]]; then
    note=" (new folder)"
    if ((APPLY == 1)); then
      mkdir -p "$destination_dir"
    fi
  fi
  changes="$(rsync "${RSYNC_ARGS[@]}" "${STAGED_DIRS[$service]}/" "$destination_dir/")"
  if [[ -n "$changes" ]]; then
    echo "$changes"
  fi
  added="$(grep -c '^>f+++++++' <<< "$changes" || true)"
  updated="$(grep -c '^>f[^+]' <<< "$changes" || true)"
  deleted="$(grep -c '^\*deleting' <<< "$changes" || true)"
  blocked=""
  for blocked_service in "${BLOCKED_SERVICES[@]}"; do
    [[ "$blocked_service" == "$service" ]] && blocked=" REFUSED-ON-APPLY(dirty)"
  done
  SUMMARY+=("$service: +$added new, ~$updated changed, -$deleted deleted$note$blocked")
done

echo ">> summary"
printf '   %s\n' "${SUMMARY[@]}"

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
