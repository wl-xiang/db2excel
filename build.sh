#!/usr/bin/env bash
# =============================================================================
# oracle2excel 一键构建脚本
#   1. docker build 构建镜像（Dockerfile 构建阶段在线下载 Oracle Instant Client 19）
#   2. 验证镜像内 Thick 模式能否加载 libclntsh
#   3. docker save | gzip 打包为 TGZ 到 dist/ 目录，并输出 sha256
#
# 用法:
#   ./build.sh              # 默认版本 1.0.0
#   ./build.sh 1.2.0        # 指定版本号
#   INSTANTCLIENT_URL=<下载地址> ./build.sh   # 指定 Instant Client 下载源（可选）
#
# 导入镜像（在部署机上）:
#   docker load -i dist/oracle2excel_<版本>_linux_amd64.tar.gz
# =============================================================================
set -euo pipefail

VERSION="${1:-1.0.0}"
IMAGE_NAME="oracle2excel"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIST_DIR="${PROJECT_DIR}/dist"

log() { echo -e "\033[1;34m[build]\033[0m $*"; }
die() { echo -e "\033[1;31m[build] 错误:\033[0m $*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# 0. 前置检查
# ---------------------------------------------------------------------------
command -v docker >/dev/null 2>&1 || die "未检测到 docker，请先安装 Docker Engine"
docker info >/dev/null 2>&1 || die "docker daemon 不可用（服务未启动或无权限）"

mkdir -p "${DIST_DIR}"

# ---------------------------------------------------------------------------
# 1. 构建镜像（Instant Client 在 Dockerfile 构建阶段在线下载）
# ---------------------------------------------------------------------------
BUILD_ARGS=()
if [[ -n "${INSTANTCLIENT_URL:-}" ]]; then
    BUILD_ARGS+=(--build-arg "INSTANTCLIENT_URL=${INSTANTCLIENT_URL}")
fi

log "开始构建镜像 ${IMAGE_NAME}:${VERSION} ..."
docker build "${BUILD_ARGS[@]}" -t "${IMAGE_NAME}:${VERSION}" -f "${PROJECT_DIR}/Dockerfile" "${PROJECT_DIR}"

# ---------------------------------------------------------------------------
# 2. 验证镜像内 Instant Client 与驱动（Thick 模式能否真正加载 libclntsh）
# ---------------------------------------------------------------------------
log "验证镜像..."
docker run --rm -i --entrypoint python "${IMAGE_NAME}:${VERSION}" - <<'PYEOF'
import os, oracledb
assert os.path.isdir(os.environ["ORACLE_HOME"]), "ORACLE_HOME 不存在"
assert os.path.isfile(os.path.join(os.environ["ORACLE_HOME"], "libclntsh.so.19.1")), "libclntsh.so.19.1 不存在"
oracledb.init_oracle_client()  # 默认从 ORACLE_HOME/LD_LIBRARY_PATH 找库
print("[verify] Thick 模式 OK, Instant Client:", oracledb.clientversion())
PYEOF
log "镜像内 Thick 模式验证通过"

# ---------------------------------------------------------------------------
# 3. 导出 TGZ
# ---------------------------------------------------------------------------
TGZ="${DIST_DIR}/${IMAGE_NAME}_${VERSION}_linux_amd64.tar.gz"
log "导出镜像到 ${TGZ} ..."
docker save "${IMAGE_NAME}:${VERSION}" | gzip > "${TGZ}"

SIZE=$(du -h "${TGZ}" | cut -f1)
SHA=$(sha256sum "${TGZ}" | cut -d' ' -f1)
log "构建完成!"
echo "  镜像:   ${IMAGE_NAME}:${VERSION}"
echo "  TGZ:    ${TGZ} (${SIZE})"
echo "  SHA256: ${SHA}"
echo
echo "部署机导入方式:"
echo "  docker load -i ${TGZ##*/}"
echo "  docker compose up -d   # 或 docker compose up（once 模式前台运行）"
