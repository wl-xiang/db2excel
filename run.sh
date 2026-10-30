#!/usr/bin/env bash
# =============================================================================
# oracle2excel 一次性运行脚本（跑完即删容器）
#
# 用法:
#   ./run.sh                 # 默认镜像 oracle2excel:1.0.0
#   ./run.sh 1.2.0           # 指定版本号
#   NETWORK=host ./run.sh    # Oracle 在宿主机上时用 host 网络（Linux 专用）
#   LOG_LEVEL=DEBUG ./run.sh # 调试日志
#
# 说明:
#   - 配置读取 ./config/oracle_conn.json，xlsx 输出到 ./output/
#   - 容器以 uid=10001 运行；如遇 output 无写权限: sudo chown -R 10001:10001 output
#   - 退出码即容器退出码: 0 成功 / 2 配置错误 / 3 连接失败 / 4 导出失败
# =============================================================================
set -euo pipefail

VERSION="${1:-1.0.0}"
IMAGE="oracle2excel:${VERSION}"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NETWORK="${NETWORK:-bridge}"
LOG_LEVEL="${LOG_LEVEL:-INFO}"
NAME="oracle2excel-run-$(date +%s)"

log() { echo -e "\033[1;34m[run]\033[0m $*"; }

command -v docker >/dev/null 2>&1 || { echo "未检测到 docker"; exit 1; }
docker image inspect "${IMAGE}" >/dev/null 2>&1 \
    || { echo "镜像不存在: ${IMAGE}，请先 ./build.sh 构建或 docker load 导入"; exit 1; }

mkdir -p "${PROJECT_DIR}/output"

log "启动一次性导出: ${IMAGE} (network=${NETWORK}, log=${LOG_LEVEL})"
log "配置: ${PROJECT_DIR}/config/oracle_conn.json"
log "输出: ${PROJECT_DIR}/output/"

EXIT_CODE=0
docker run --rm \
    --name "${NAME}" \
    --network "${NETWORK}" \
    -e TZ=Asia/Shanghai \
    -e LOG_LEVEL="${LOG_LEVEL}" \
    -e CONFIG_PATH=/app/config/oracle_conn.json \
    -v "${PROJECT_DIR}/config:/app/config:ro" \
    -v "${PROJECT_DIR}/output:/app/output" \
    "${IMAGE}" || EXIT_CODE=$?

case "${EXIT_CODE}" in
    0) log "导出成功 ✔ 结果见 ${PROJECT_DIR}/output/" ;;
    2) log "失败: 配置错误（退出码 2），检查 config/oracle_conn.json" ;;
    3) log "失败: Oracle 连接失败（退出码 3），检查 host/port/service_name/网络可达性" ;;
    4) log "失败: 部分导出任务出错（退出码 4），见上方日志" ;;
    *) log "异常退出，退出码 ${EXIT_CODE}" ;;
esac
exit "${EXIT_CODE}"
