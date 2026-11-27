#!/usr/bin/env bash
# Start the built image without a network or published ports, then verify restart persistence.
set -euo pipefail

image=${1:?usage: bash scripts/smoke_production.sh IMAGE CONTAINER_NAME}
container=${2:?usage: bash scripts/smoke_production.sh IMAGE CONTAINER_NAME}
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)

if docker container inspect "$container" >/dev/null 2>&1; then
  echo "Container already exists: $container" >&2
  exit 1
fi

created=false
cleanup() {
  status=$?
  trap - EXIT
  if "$created"; then
    docker logs "$container" || true
    docker rm --force --volumes "$container" || status=1
  fi
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

docker create --name "$container" --network none \
  --env TINDALOS_LLM_ENABLED=0 \
  --env TINDALOS_DATA_DIR=/tmp/tindalos-smoke-data \
  --env TINDALOS_SITE_DB=/tmp/tindalos-smoke-data/site.db \
  "$image"
created=true
docker start "$container"
timeout 90s docker exec --interactive "$container" python - \
  --receipt /tmp/tindalos-smoke-receipt.json < "$script_dir/smoke_production.py"
docker restart --time 10 "$container"
timeout 90s docker exec --interactive "$container" python - \
  --receipt /tmp/tindalos-smoke-receipt.json --verify-persisted < "$script_dir/smoke_production.py"
