#!/usr/bin/env bash
# ⚡ 一键发布 Talking-Head Recut 到 Smithery（URL 远程发布）
#
# 前置：先登录 ——  smithery auth login   （浏览器 OAuth，需用户手动完成）
# 用法： bash publish-smithery.sh
# 可选： SMITHERY_NS=your-org 覆盖命名空间（默认 qianqiuwanzi，自动探测登录账号）
#
set -euo pipefail

SERVER_URL="https://www.qianshi.cool/thapi/mcp"
SERVER_SLUG="th-recut-mcp"
SCHEMA_FILE="$(dirname "$0")/smithery-config-schema.json"

echo "==> 检查 Smithery 登录态 ..."
WHO=$(smithery auth whoami 2>/dev/null || true)
if [ -z "$WHO" ]; then
  echo "❌ 未登录。请先运行： smithery auth login  （浏览器完成 OAuth 后重试）"
  exit 1
fi
echo "    已登录：$WHO"

# 命名空间：优先环境变量，其次 whoami 输出的 Namespace 行，兜底 105920869
# ⚠️ 2026-10-28 实测：`smithery auth whoami` 输出的是 "Namespace: 105920869"（不是 @handle），
#    旧脚本用 grep '@[A-Za-z0-9_-]+' 永远匹配不到 → 会错误回落到 qianqiuwanzi。
NS="${SMITHERY_NS:-105920869}"
DET=$(printf '%s' "$WHO" | grep -oE 'Namespace:[[:space:]]*[A-Za-z0-9_-]+' | head -1 | sed 's/Namespace:[[:space:]]*//' || true)
[ -n "$DET" ] && NS="$DET"
echo "    命名空间：@$NS"

# 校验 schema 文件
if [ ! -f "$SCHEMA_FILE" ]; then
  echo "❌ 找不到 $SCHEMA_FILE"
  exit 1
fi

echo "==> 发布到 Smithery：@$NS/$SERVER_SLUG"
echo "    URL: $SERVER_URL"
echo "    (Smithery 会读取 https://www.qianshi.cool/.well-known/mcp/server-card.json 获取工具元信息，无需鉴权扫描)"
smithery mcp publish "$SERVER_URL" -n "$NS/$SERVER_SLUG" --config-schema "$SCHEMA_FILE" --verbose

echo "✅ 发布已提交。到 https://smithery.ai/@$NS/$SERVER_SLUG 查看服务器页。"
echo "   若提示 paused / 需要授权，可再次运行并加 --resume。"
