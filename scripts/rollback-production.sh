#!/usr/bin/env bash
set -Eeuo pipefail

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

script_dir=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(CDPATH= cd -- "$script_dir/.." && pwd)
compose_file="$repo_root/docker-compose.prod.yml"
state_dir=${DEPLOY_STATE_DIR:-/home/yananliu/.local/state/doctrans}
state_file="$state_dir/last-deploy.env"

[[ ${CONFIRM_PRODUCTION:-} == YES ]] || fail "Set CONFIRM_PRODUCTION=YES to confirm the rollback"
[[ -f "$state_file" ]] || fail "Deployment state not found: $state_file"

# shellcheck disable=SC1090
source "$state_file"
[[ -n ${ROLLBACK_VERSION:-} ]] || fail "No previous image was recorded"
[[ -f ${COMPOSE_ENV_FILE:-} ]] || fail "Compose environment file is missing"
docker image inspect "doctrans:$ROLLBACK_VERSION" >/dev/null 2>&1 || fail "Rollback image is missing"

compose() {
  DOCTRANS_VERSION="$ROLLBACK_VERSION" docker compose \
    --project-name doctrans \
    --env-file "$COMPOSE_ENV_FILE" \
    --file "$compose_file" \
    "$@"
}

docker rm --force doctrans >/dev/null 2>&1 || true

if [[ ${RESTORE_DATA:-NO} == YES ]]; then
  [[ -f ${BACKUP_ARCHIVE:-} ]] || fail "Backup archive is missing"
  timestamp=$(date +%Y%m%d-%H%M%S)
  preserved_data="$(dirname -- "$BACKUP_ARCHIVE")/data-before-rollback-$timestamp"
  mv "$repo_root/data" "$preserved_data"
  tar -C "$repo_root" -xzf "$BACKUP_ARCHIVE"
  echo "Current data preserved at $preserved_data"
fi

compose up -d --no-deps --force-recreate doctrans

for _ in $(seq 1 24); do
  status=$(docker container inspect doctrans --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}' 2>/dev/null || true)
  if [[ "$status" == healthy ]]; then
    echo "Rollback succeeded: doctrans:$ROLLBACK_VERSION"
    exit 0
  fi
  [[ "$status" != unhealthy ]] || break
  sleep 5
done

compose logs --tail=200 doctrans >&2 || true
fail "Rollback container did not become healthy"
