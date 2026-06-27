#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
IMAGE="${APPV23_IMAGE:-${APPV23_SANDBOX_IMAGE:-ghcr.io/htooayelwinict/appv23:production}}"
NPM_PREFIX="${APPV23_NPM_PREFIX:-}"

need_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Error: required command not found: $1" >&2
    exit 1
  fi
}

need_cmd docker
need_cmd npm
need_cmd node

if ! docker info >/dev/null 2>&1; then
  echo "Error: Docker is not running or is not reachable." >&2
  exit 1
fi

if [[ "${APPV23_BUILD_LOCAL:-0}" == "1" || "${APPV23_REBUILD_IMAGE:-0}" == "1" ]]; then
  echo "Building local Docker image: ${IMAGE}"
  docker build --pull=false -f "${ROOT_DIR}/appV2.3/Dockerfile.appv23" -t "${IMAGE}" "${ROOT_DIR}/appV2.3"
else
  echo "Pulling Docker image: ${IMAGE}"
  docker pull "${IMAGE}"
fi

echo "Installing global appv23-sandbox command"
if [[ -n "${NPM_PREFIX}" ]]; then
  npm install --global --prefix "${NPM_PREFIX}" "${ROOT_DIR}"
  BIN_PATH="${NPM_PREFIX}/bin/appv23-sandbox"
else
  npm install --global "${ROOT_DIR}"
  GLOBAL_PREFIX="$(npm prefix --global)"
  BIN_PATH="${GLOBAL_PREFIX}/bin/appv23-sandbox"
fi

if command -v appv23-sandbox >/dev/null 2>&1; then
  VERIFY_BIN="appv23-sandbox"
elif [[ -x "${BIN_PATH}" ]]; then
  VERIFY_BIN="${BIN_PATH}"
else
  echo "Error: appv23-sandbox was installed but was not found on PATH." >&2
  echo "Check npm global bin path or set APPV23_NPM_PREFIX before installing." >&2
  exit 1
fi

"${VERIFY_BIN}" --help >/dev/null
"${VERIFY_BIN}" --cwd "${ROOT_DIR}/docs" --dry-run >/dev/null

echo "Installed appv23 sandbox."
echo "Run from anywhere:"
echo "  appv23-sandbox --cwd /path/to/workspace"

if [[ "${VERIFY_BIN}" != "appv23-sandbox" ]]; then
  echo "Note: appv23-sandbox is installed at ${VERIFY_BIN}, but that directory is not on PATH."
fi
