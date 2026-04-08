#!/usr/bin/env bash
set -e

# NoCode Agent - 一键启动脚本
# Usage: ./start.sh [--resume]

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# ── 1. 检查 Python ──
if ! command -v python3 &>/dev/null; then
    error "未找到 python3，请先安装 Python >= 3.12"
fi

PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
if python3 -c "import sys; exit(0 if sys.version_info >= (3, 12) else 1)"; then
    info "Python $PY_VERSION ✓"
else
    error "需要 Python >= 3.12，当前版本 $PY_VERSION"
fi

# ── 2. 检查 Node.js ──
if ! command -v node &>/dev/null; then
    error "未找到 node，请先安装 Node.js >= 20"
fi
info "Node $(node -v) ✓"

# ── 3. 创建虚拟环境并安装依赖 ──
if [ ! -d ".venv" ]; then
    info "创建虚拟环境..."
    python3 -m venv .venv
fi

source .venv/bin/activate

# 检查是否需要安装/更新依赖
if ! python3 -c "import nocode_agent" 2>/dev/null || [ "pyproject.toml" -nt ".venv/lib/python"* ]; then
    info "安装 Python 依赖..."
    pip install -e . -q
fi

# ── 4. 配置文件检查 ──
if [ ! -f "nocode_agent/config.yaml" ]; then
    if [ -f "nocode_agent/config.example.yaml" ]; then
        warn "未找到 config.yaml，从模板创建..."
        cp nocode_agent/config.example.yaml nocode_agent/config.yaml
        warn "请编辑 nocode_agent/config.yaml 填入你的 API key"
        warn "  vim nocode_agent/config.yaml"
        echo ""
        read -p "是否现在编辑？[Y/n] " choice
        case "$choice" in
            n|N) ;;
            *) ${EDITOR:-vim} nocode_agent/config.yaml ;;
        esac
    else
        warn "未找到 config.yaml，将使用环境变量中的 API key"
    fi
fi

# ── 5. 启动 ──
info "启动 NoCode Agent..."
exec bin/nocode "$@"
