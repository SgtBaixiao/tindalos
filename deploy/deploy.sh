#!/usr/bin/env bash
#
# Tindalos 一键部署脚本（在 Ubuntu VPS 上运行）
#
# ============================================================
# 在哪里运行：Ubuntu VPS（或任意装了 bash + docker 的 Linux 服务器）上的仓库目录。
#            本机是 Windows/开发机，请不要直接跑这个脚本——它是给服务器用的。
#
# 如何给执行权限并运行（在仓库根目录下）：
#     chmod +x deploy/deploy.sh
#     ./deploy/deploy.sh
#
# 前置要求：已安装 Docker 及 docker compose 插件
#    （脚本会自动检查；缺失 docker 时会打印一键安装指引）。
#
# 脚本做什么：
#   1. 检查 git / docker / docker compose 插件是否可用；
#   2. 定位仓库根目录（脚本位于 deploy/ 下，仓库根为其上一级）；
#   3. 首次运行自动从 deploy/.env.example 生成 deploy/.env，
#      并交互式询问 DeepSeek API key 填入（已设环境变量 TINDALOS_API_KEY 则直接复用）；
#   4. docker compose 构建并后台启动服务（端口 8347）；
#   5. 轮询等待 http://127.0.0.1:8347/api/health 就绪（最长 60 秒）；
#   6. 成功后打印访问地址与常用运维命令；失败时打印错误并提示日志命令。
#
# 安全约定：脚本内绝不打印 API key 明文；deploy/.env 权限设为 600（仅属主可读写）。
# ============================================================

set -euo pipefail

# ---------------------------------------------------------------- 工具函数

# 中文进度提示（带时间戳 + 青色）
print_step() {
    printf '\n\033[1;36m[%s]\033[0m %s\n' "$(date '+%H:%M:%S')" "$1"
}

# 打印错误并退出（非 0 状态）
fail() {
    printf '\033[1;31m错误：%s\033[0m\n' "$1" >&2
    exit 1
}

# ---------------------------------------------------------------- 0. 路径定位

# 脚本放在 deploy/ 下 → 仓库根 = 脚本所在目录的上一级（绝对路径）
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEPLOY_DIR="$ROOT/deploy"
COMPOSE_FILE="$DEPLOY_DIR/docker-compose.yml"
ENV_FILE="$DEPLOY_DIR/.env"

cd "$ROOT"
print_step "仓库根目录：$ROOT"

# ---------------------------------------------------------------- 1. 依赖检查

print_step "检查依赖：git / docker / docker compose 插件"

if ! command -v git >/dev/null 2>&1; then
    fail "未找到 git。请先安装：sudo apt-get update && sudo apt-get install -y git"
fi
echo "  ✓ git"

if ! command -v docker >/dev/null 2>&1; then
    echo "  ✗ 未找到 docker。请执行以下命令安装（Ubuntu 官方一键脚本）："
    echo
    echo "      curl -fsSL https://get.docker.com | sh"
    echo
    echo "  安装完成后，把当前用户加入 docker 组（之后需要重新登录/重开终端生效）："
    echo
    echo "      sudo usermod -aG docker \"$USER\""
    echo
    fail "docker 未安装，请按上述指引安装后重试本脚本。"
fi
echo "  ✓ docker"

if ! docker compose version >/dev/null 2>&1; then
    echo "  ✗ docker compose 插件不可用。请先安装 compose 插件："
    echo
    echo "      sudo apt-get update && sudo apt-get install -y docker-compose-plugin"
    echo
    echo "  （若此前通过 https://get.docker.com 安装 Docker，compose 插件通常已附带；"
    echo "    安装后可用 docker compose version 验证。）"
    fail "docker compose 插件缺失，请安装后重试本脚本。"
fi
echo "  ✓ docker compose 插件"

# 构建/编排依赖的 compose 与 Dockerfile 必须就位（缺失时给出明确提示）
if [ ! -f "$COMPOSE_FILE" ]; then
    fail "未找到 $COMPOSE_FILE —— 请确认仓库完整（deploy/docker-compose.yml 缺失）。"
fi
if [ ! -f "$DEPLOY_DIR/Dockerfile" ]; then
    fail "未找到 $DEPLOY_DIR/Dockerfile —— compose 构建需要它，请确认仓库完整。"
fi

# ---------------------------------------------------------------- 2. 配置 .env

print_step "配置环境变量（deploy/.env）"

needs_key=0   # 1 = 需要（重新）填写 API key

if [ ! -f "$ENV_FILE" ]; then
    if [ ! -f "$DEPLOY_DIR/.env.example" ]; then
        fail "未找到 $DEPLOY_DIR/.env.example，无法生成 .env。"
    fi
    cp "$DEPLOY_DIR/.env.example" "$ENV_FILE"
    echo "  ✓ 已从 .env.example 生成 deploy/.env"
    needs_key=1
fi

# .env 含 API key：权限收为 600（仅属主可读写）
chmod 600 "$ENV_FILE"

# 判断当前 .env 里的 key 是否仍是占位符或为空（是 → 需要填真实 key）
current_key="$(grep -E '^TINDALOS_API_KEY=' "$ENV_FILE" | head -n 1 | cut -d= -f2- || true)"
if [ -z "$current_key" ] || [ "$current_key" = "sk-在这里填你的DeepSeekKey" ]; then
    needs_key=1
fi

API_KEY=""
if [ -n "${TINDALOS_API_KEY:-}" ]; then
    # 环境变量已有 → 直接复用（不打印明文）
    API_KEY="$TINDALOS_API_KEY"
    echo "  ✓ 检测到环境变量 TINDALOS_API_KEY，直接复用（不显示明文）"
elif [ "$needs_key" -eq 1 ] && [ -t 0 ]; then
    # 交互式终端：静默读入（输入不回显，回车确认；留空 = 跳过）
    printf '  请输入 DeepSeek API key（输入不可见，回车确认；直接回车=跳过）: '
    if read -r API_KEY; then
        printf '\n'
    else
        API_KEY=""
        printf '\n'
    fi
elif [ "$needs_key" -eq 1 ]; then
    # 非交互式终端（CI / 管道）：跳过输入
    echo "  ! 检测到非交互式终端，跳过 API key 输入。"
else
    echo "  ✓ deploy/.env 已有 API key 配置，跳过输入"
fi

if [ -z "$API_KEY" ] && [ "$needs_key" -eq 1 ]; then
    echo "  ! 未提供 API key：LLM 云端生成不可用（生成会回退到离线确定性模板），服务本身仍可启动。"
fi

# 把真实 key 写回 .env 的 TINDALOS_API_KEY= 行（sed 替换串特殊字符已转义）
if [ -n "$API_KEY" ]; then
    escaped="$(printf '%s' "$API_KEY" | sed -e 's/[&|\\]/\\&/g')"
    if grep -q '^TINDALOS_API_KEY=' "$ENV_FILE"; then
        sed -i "s|^TINDALOS_API_KEY=.*|TINDALOS_API_KEY=${escaped}|" "$ENV_FILE"
    else
        printf 'TINDALOS_API_KEY=%s\n' "$API_KEY" >> "$ENV_FILE"
    fi
    chmod 600 "$ENV_FILE"
    echo "  ✓ API key 已写入 deploy/.env（权限 600）"
fi

echo "  ! 提醒：deploy/.env 含敏感凭据，请勿提交 git（仓库 .gitignore 已排除 deploy/.env）。"

# ---------------------------------------------------------------- 3. 构建并启动

print_step "docker compose 构建并后台启动（首次构建较久，请耐心等待）"

if ! docker compose -f "$COMPOSE_FILE" up -d --build; then
    echo
    echo "构建/启动失败。请用以下命令查看日志："
    echo "    docker compose -f deploy/docker-compose.yml logs -f"
    fail "docker compose up 失败（见上方输出）。"
fi

# ---------------------------------------------------------------- 4. 等待健康

print_step "等待服务就绪（最长 60 秒）：http://127.0.0.1:8347/api/health"

SECONDS=0
until curl -sf http://127.0.0.1:8347/api/health >/dev/null 2>&1; do
    if [ "$SECONDS" -ge 60 ]; then
        echo
        echo "服务在 60 秒内未就绪。最近日志如下："
        docker compose -f "$COMPOSE_FILE" logs --tail=60 2>&1 || true
        fail "服务健康检查超时。请用 docker compose -f deploy/docker-compose.yml logs -f 排查。"
    fi
    sleep 2
done

# ---------------------------------------------------------------- 5. 成功输出

print_step "部署成功，服务已就绪 ✓"

# 取服务器首个 IP（hostname -I 输出多个 IP，空格分隔取第一个）
SERVER_IP="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
if [ -z "$SERVER_IP" ]; then
    SERVER_IP="<服务器IP>"   # 取不到时给占位，提示用户自行替换
fi

echo
echo "  访问地址：  http://${SERVER_IP}:8347"
echo "  本机调试：  http://127.0.0.1:8347"
echo
echo "  常用命令（在仓库根目录执行）："
echo "    查看日志： docker compose -f deploy/docker-compose.yml logs -f"
echo "    重启服务： docker compose -f deploy/docker-compose.yml restart"
echo "    停止服务： docker compose -f deploy/docker-compose.yml down"
echo
