# 基础LangChain项目模板

## 项目结构
```
project-name/
├── src/
│   ├── __init__.py
│   ├── agent.py          # 代理实现
│   ├── prompts.py        # 提示词管理
│   └── tools.py          # 工具定义
├── tests/
│   └── __init__.py
├── data/
├── docs/
├── requirements.txt
├── pyproject.toml
└── main.py
```

## 快速配置

### 1. 环境配置
```bash
# 创建虚拟环境
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 安装依赖
pip install -r requirements.txt
```

### 2. API配置
创建 `.env` 文件：
```
OPENAI_API_KEY=your-api-key-here
OPENAI_BASE_URL=https://api.openai.com/v1
```

### 3. 运行项目
```bash
python main.py
```

## 核心功能

- ✅ 完整的代理架构
- ✅ 工具系统集成
- ✅ 技能系统支持
- ✅ 配置文件管理
- ✅ 测试框架集成