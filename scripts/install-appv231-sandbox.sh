#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
IMAGE="${APPV231_IMAGE:-${APPV231_SANDBOX_IMAGE:-ghcr.io/htooayelwinict/appv231:production}}"
NPM_PREFIX="${APPV231_NPM_PREFIX:-}"

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

if [[ "${APPV231_BUILD_LOCAL:-0}" == "1" || "${APPV231_REBUILD_IMAGE:-0}" == "1" ]]; then
  echo "Building local Docker image: ${IMAGE}"
  docker build --pull=false -f "${ROOT_DIR}/appV2.3.1/Dockerfile.appv231" -t "${IMAGE}" "${ROOT_DIR}/appV2.3.1"
else
  echo "Pulling Docker image: ${IMAGE}"
  pull_image() {
    if [[ -n "${APPV231_DOCKER_CONFIG:-}" ]]; then
      DOCKER_CONFIG="${APPV231_DOCKER_CONFIG}" docker pull "${IMAGE}"
      return
    fi
    if [[ -z "${DOCKER_CONFIG:-}" && "${IMAGE}" == ghcr.io/htooayelwinict/appv231:* ]]; then
      local tmp_docker_config
      local pull_rc
      tmp_docker_config="$(mktemp -d)"
      DOCKER_CONFIG="${tmp_docker_config}" docker pull "${IMAGE}"
      pull_rc=$?
      rm -rf "${tmp_docker_config}"
      return "${pull_rc}"
    fi
    docker pull "${IMAGE}"
  }
  pull_image
fi

echo "Installing global appv231-sandbox command"
if [[ -n "${NPM_PREFIX}" ]]; then
  npm install --global --prefix "${NPM_PREFIX}" "${ROOT_DIR}"
  BIN_PATH="${NPM_PREFIX}/bin/appv231-sandbox"
else
  npm install --global "${ROOT_DIR}"
  GLOBAL_PREFIX="$(npm prefix --global)"
  BIN_PATH="${GLOBAL_PREFIX}/bin/appv231-sandbox"
fi

if command -v appv231-sandbox >/dev/null 2>&1; then
  VERIFY_BIN="appv231-sandbox"
elif [[ -x "${BIN_PATH}" ]]; then
  VERIFY_BIN="${BIN_PATH}"
else
  echo "Error: appv231-sandbox was installed but was not found on PATH." >&2
  echo "Check npm global bin path or set APPV231_NPM_PREFIX before installing." >&2
  exit 1
fi

"${VERIFY_BIN}" --help >/dev/null
"${VERIFY_BIN}" --cwd "${ROOT_DIR}/docs" --dry-run >/dev/null

echo "Installed appv231 sandbox."
echo "Run from anywhere:"
echo "  appv231-sandbox --cwd /path/to/workspace"

if [[ "${VERIFY_BIN}" != "appv231-sandbox" ]]; then
  echo "Note: appv231-sandbox is installed at ${VERIFY_BIN}, but that directory is not on PATH."
fi
