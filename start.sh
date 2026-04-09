#!/usr/bin/env bash
set -e

# NoCode Agent - 一键启动脚本
# Usage: ./start.sh [--resume] [--install]

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# ── 安装到 PATH ──
install_to_path() {
    local NOCODE_BIN="$SCRIPT_DIR/bin"

    # 检测当前 shell 配置文件
    local SHELL_RC=""
    if [ -n "$ZSH_VERSION" ]; then
        SHELL_RC="$HOME/.zshrc"
    elif [ -n "$BASH_VERSION" ]; then
        SHELL_RC="$HOME/.bashrc"
    fi

    if [ -z "$SHELL_RC" ]; then
        warn "无法检测 shell 类型，请手动添加以下内容到你的 shell 配置文件："
        echo -e "  ${CYAN}export PATH=\"$NOCODE_BIN:\$PATH\"${NC}"
        return
    fi

    # 检查是否已配置
    if grep -q "$NOCODE_BIN" "$SHELL_RC" 2>/dev/null; then
        info "PATH 已配置（$SHELL_RC），无需重复添加"
    else
        echo "" >> "$SHELL_RC"
        echo "# NoCode Agent" >> "$SHELL_RC"
        echo "export PATH=\"$NOCODE_BIN:\$PATH\"" >> "$SHELL_RC"
        info "已将 $NOCODE_BIN 添加到 $SHELL_RC"
    fi

    # 当前会话也生效
    export PATH="$NOCODE_BIN:$PATH"
    info "现在可以直接在命令行运行 ${CYAN}nocode${NC} 启动了！"
    info "（新终端窗口自动生效）"
}

# ── 检查 --install 参数 ──
for arg in "$@"; do
    case "$arg" in
        --install)
            install_to_path
            exit 0
            ;;
    esac
done

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
    error "未找到 node，请先安装 Node.js"
fi
info "Node $(node -v) ✓"

# ── 3. 安装 Node.js 依赖 ──
if [ ! -x "node_modules/.bin/tsx" ]; then
    info "安装 Node.js 依赖..."
    npm install
fi

# ── 4. 创建虚拟环境并安装依赖 ──
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

# ── 5. 配置文件检查 ──
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

# ── 6. 提示安装到 PATH ──
if ! command -v nocode &>/dev/null; then
    echo ""
    warn "nocode 命令未加入 PATH"
    info "运行 ${CYAN}./start.sh --install${NC} 可添加到环境变量，之后直接 nocode 启动"
    echo ""
fi

# ── 7. 启动 ──
info "启动 NoCode Agent..."
exec bin/nocode "$@"
