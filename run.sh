#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${CYAN}"
echo "╔══════════════════════════════════╗"
echo "║        Best Friend AI 启动中      ║"
echo "╚══════════════════════════════════╝"
echo -e "${NC}"

# 1. Find Python 3.12+
PYTHON=""
for cmd in python3.12 python3.13 python3; do
    if command -v "$cmd" &>/dev/null; then
        version=$($cmd --version 2>&1 | awk '{print $2}')
        major=$(echo "$version" | cut -d. -f1)
        minor=$(echo "$version" | cut -d. -f2)
        if [ "$major" -ge 3 ] && [ "$minor" -ge 12 ]; then
            PYTHON="$cmd"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    echo -e "${RED}Error: 需要 Python 3.12+，请先安装:${NC}"
    echo "  brew install python@3.12"
    exit 1
fi

echo -e "${GREEN}✓${NC} Python: $($PYTHON --version)"

# 2. Create venv if not exists
if [ ! -d ".venv" ]; then
    echo -e "${YELLOW}→${NC} 创建虚拟环境..."
    "$PYTHON" -m venv .venv
fi

source .venv/bin/activate
echo -e "${GREEN}✓${NC} 虚拟环境已激活"

# 3. Install dependencies if needed
if ! python -c "import openai" &>/dev/null; then
    echo -e "${YELLOW}→${NC} 安装依赖..."
    pip install -e ".[dev]" -q
fi
echo -e "${GREEN}✓${NC} 依赖就绪"

# 4. Check API key
if [ -z "$ZHIPU_API_KEY" ]; then
    # Try loading from .env
    if [ -f ".env" ]; then
        export $(grep -v '^#' .env | xargs)
    fi
fi

if [ -z "$ZHIPU_API_KEY" ]; then
    echo ""
    echo -e "${RED}请设置智谱AI API密钥:${NC}"
    echo "  方式1: export ZHIPU_API_KEY=\"你的密钥\""
    echo "  方式2: 在 .env 文件中写入 ZHIPU_API_KEY=你的密钥"
    echo ""
    echo "  获取密钥: https://open.bigmodel.cn/"
    exit 1
fi

echo -e "${GREEN}✓${NC} API Key 已配置"
echo ""

# 5. Run
python -m src.cli "$@"
