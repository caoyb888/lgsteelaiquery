#!/bin/bash
# 莱钢 AI 问数停止脚本
# 用法：
#   ./scripts/stop.sh        # 停止生产环境（默认）
#   ./scripts/stop.sh dev    # 停止开发环境
#   ./scripts/stop.sh prod   # 停止生产环境（显式指定）

set -e

PROJECT="lgsteel"
ENV="${1:-prod}"

if [[ "$ENV" != "dev" && "$ENV" != "prod" ]]; then
    echo "❌ 参数错误：ENV 必须为 dev 或 prod，当前为 '${ENV}'"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "================================================"
echo " 莱钢 AI 问数 (${PROJECT}) - ${ENV} 环境停止"
echo "================================================"
echo ""

if [ "$ENV" = "dev" ]; then
    docker compose \
        -p "${PROJECT}_dev" \
        -f "${ROOT_DIR}/docker-compose.dev.yml" \
        down
    echo ""
    echo "✅ 开发环境已停止。"
else
    docker compose \
        -p "${PROJECT}" \
        -f "${ROOT_DIR}/docker-compose.yml" \
        down
    echo ""
    echo "✅ 生产环境已停止。"
fi
