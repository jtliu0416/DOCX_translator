#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  echo "Usage: CONFIRM_PRODUCTION=YES $0 <release-tag>" >&2
  echo "Example: CONFIRM_PRODUCTION=YES $0 v0.4.0" >&2
}

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "Required command not found: $1"
}

script_dir=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(CDPATH= cd -- "$script_dir/.." && pwd)
compose_file="$repo_root/docker-compose.prod.yml"
compose_env_file=${COMPOSE_ENV_FILE:-/home/yananliu/.config/doctrans/compose.env}
backup_root=${BACKUP_ROOT:-/home/yananliu/backups/doctrans}
state_dir=${DEPLOY_STATE_DIR:-/home/yananliu/.local/state/doctrans}
state_file="$state_dir/last-deploy.env"
release=${1:-}

[[ -n "$release" ]] || { usage; exit 2; }
[[ "$release" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || fail "Invalid release tag: $release"
[[ ${CONFIRM_PRODUCTION:-} == YES ]] || fail "Set CONFIRM_PRODUCTION=YES after confirming active jobs are drained"
[[ "$repo_root" != / ]] || fail "Refusing to deploy from the filesystem root"

for command_name in git docker tar awk; do
  require_command "$command_name"
done
docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is required"

[[ -f "$compose_file" ]] || fail "Missing $compose_file"
[[ -f "$compose_env_file" ]] || fail "Missing $compose_env_file (start from deploy/compose.env.example)"
[[ -f "$repo_root/backend/.env" ]] || fail "Missing production backend/.env"

set -a
# shellcheck disable=SC1090
source "$compose_env_file"
set +a

webui_origins=${VITE_WEBUI_ORIGINS:-${VITE_WEBUI_ORIGIN:-}}
[[ -n "$webui_origins" ]] || fail "VITE_WEBUI_ORIGINS is required"
normalized_origins=""
IFS=',' read -r -a origin_list <<<"$webui_origins"
for origin in "${origin_list[@]}"; do
  origin=${origin#"${origin%%[![:space:]]*}"}
  origin=${origin%"${origin##*[![:space:]]}"}
  origin=${origin%/}
  [[ "$origin" =~ ^https?://[A-Za-z0-9._:-]+$ ]] || \
    fail "Invalid WebUI origin: $origin"
  [[ -z "$normalized_origins" ]] || normalized_origins+=,
  normalized_origins+="$origin"
done
VITE_WEBUI_ORIGINS=$normalized_origins
export VITE_WEBUI_ORIGINS

for key in JWT_SECRET JWT_ISSUER JWT_AUDIENCE CORS_ALLOWED_ORIGINS; do
  grep -Eq "^${key}=.+" "$repo_root/backend/.env" || fail "$key is missing from backend/.env"
done
jwt_secret=$(awk -F= '$1 == "JWT_SECRET" {sub(/^[^=]*=/, ""); print; exit}' "$repo_root/backend/.env")
[[ ${#jwt_secret} -ge 32 ]] || fail "JWT_SECRET must contain at least 32 characters"

git_in_repo() {
  (cd "$repo_root" && git "$@")
}

git_in_repo rev-parse --is-inside-work-tree >/dev/null 2>&1 || fail "Not a Git worktree"
[[ -z $(git_in_repo status --porcelain --untracked-files=normal) ]] || fail "Git worktree is not clean"
release_commit=$(git_in_repo rev-parse --verify "refs/tags/${release}^{commit}" 2>/dev/null) || fail "Tag does not exist: $release"
head_commit=$(git_in_repo rev-parse HEAD)
[[ "$head_commit" == "$release_commit" ]] || fail "HEAD is not the commit tagged $release"

image="doctrans:$release"
if docker image inspect "$image" >/dev/null 2>&1; then
  [[ ${REUSE_IMAGE:-} == YES ]] || fail "$image already exists; use a new tag or set REUSE_IMAGE=YES"
else
  echo "Building $image from $head_commit..."
  docker build \
    --build-arg "VITE_WEBUI_ORIGINS=$VITE_WEBUI_ORIGINS" \
    --build-arg "APP_VERSION=$release" \
    --build-arg "VCS_REF=$head_commit" \
    --tag "$image" \
    "$repo_root"
fi

compose() {
  local version=$1
  shift
  DOCTRANS_VERSION="$version" docker compose \
    --project-name doctrans \
    --env-file "$compose_env_file" \
    --file "$compose_file" \
    "$@"
}

compose "$release" config --quiet

timestamp=$(date +%Y%m%d-%H%M%S)
backup_dir="$backup_root/$timestamp-$release"
backup_archive="$backup_dir/data.tgz"
rollback_version=""
old_image_id=""

mkdir -p "$backup_dir" "$state_dir" "$repo_root/data"
chmod 700 "$backup_dir" "$state_dir"

if docker container inspect doctrans >/dev/null 2>&1; then
  old_image_id=$(docker container inspect doctrans --format '{{.Image}}')
  rollback_version="rollback-$timestamp"
  docker image tag "$old_image_id" "doctrans:$rollback_version"
  echo "Stopping the current container for a consistent data backup..."
  docker stop --time 45 doctrans >/dev/null
fi

if ! tar -C "$repo_root" -czf "$backup_archive" data || \
   ! cp "$repo_root/backend/.env" "$backup_dir/backend.env"; then
  [[ -z "$old_image_id" ]] || docker start doctrans >/dev/null
  fail "Backup failed; the previous container was restarted"
fi
chmod 600 "$backup_archive" "$backup_dir/backend.env"

{
  printf 'RELEASE=%q\n' "$release"
  printf 'RELEASE_COMMIT=%q\n' "$head_commit"
  printf 'ROLLBACK_VERSION=%q\n' "$rollback_version"
  printf 'BACKUP_ARCHIVE=%q\n' "$backup_archive"
  printf 'BACKEND_ENV_BACKUP=%q\n' "$backup_dir/backend.env"
  printf 'DEPLOYED_AT=%q\n' "$timestamp"
  printf 'COMPOSE_ENV_FILE=%q\n' "$compose_env_file"
} >"$state_file"
chmod 600 "$state_file"
cp "$state_file" "$backup_dir/deploy-state.env"

if [[ -n "$old_image_id" ]]; then
  docker rm doctrans >/dev/null
fi

rollback_failed_release() {
  echo "The new container did not become healthy." >&2
  compose "$release" logs --tail=200 doctrans >&2 || true
  if [[ -n "$rollback_version" ]]; then
    echo "Restoring the pre-deployment data and image..." >&2
    docker rm --force doctrans >/dev/null 2>&1 || true
    mv "$repo_root/data" "$backup_dir/data.failed-release"
    tar -C "$repo_root" -xzf "$backup_archive"
    compose "$rollback_version" up -d --no-deps --force-recreate doctrans
  fi
  exit 1
}

echo "Starting $image..."
if ! compose "$release" up -d --no-deps --force-recreate doctrans; then
  rollback_failed_release
fi

echo "Waiting for the container health check..."
healthy=0
for _ in $(seq 1 24); do
  status=$(docker container inspect doctrans --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}' 2>/dev/null || true)
  if [[ "$status" == healthy ]]; then
    healthy=1
    break
  fi
  [[ "$status" != unhealthy ]] || break
  sleep 5
done

[[ "$healthy" == 1 ]] || rollback_failed_release

echo "Deployment succeeded."
echo "Release: $release ($head_commit)"
echo "Backup: $backup_archive"
docker ps --filter name='^/doctrans$' --format 'Container: {{.Names}}  Status: {{.Status}}  Image: {{.Image}}'
