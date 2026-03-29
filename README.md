# Best Friend - AI 智能体伙伴

一个拥有短期记忆、长期记忆、Skill 插件、定时任务能力的个人 AI 伙伴。

## 功能

- **多轮对话** — 基于 GLM（智谱AI）的智能对话
- **短期记忆** — 会话上下文管理，记住当前对话
- **长期记忆** — 持久化存储用户信息、偏好、对话历史
- **Skill 插件** — 可扩展的工具系统，支持内置和外部插件
- **定时任务** — 支持一次性/间隔/周期定时任务
- **CLI 交互** — 美观的终端对话界面

## 快速开始

### 1. 一键运行

```bash
chmod +x run.sh
./run.sh
```

首次运行会自动创建虚拟环境并安装依赖。

### 2. 手动运行

```bash
# 安装依赖
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# 设置 API Key
export ZHIPU_API_KEY="你的智谱AI密钥"

# 启动
python -m src.cli
```

## CLI 命令

| 命令 | 说明 |
|------|------|
| `/help` | 显示帮助 |
| `/skills` | 列出可用技能 |
| `/memory` | 查看长期记忆 |
| `/tasks` | 查看定时任务 |
| `/clear` | 清空短期记忆 |
| `/quit` | 退出 |

## 内置 Skills

| Skill | 说明 |
|-------|------|
| `get_current_time` | 获取当前时间（支持指定时区） |
| `get_date_info` | 获取详细日期信息 |
| `remember` | 保存信息到长期记忆 |
| `recall` | 从长期记忆中召回信息 |
| `forget` | 删除指定记忆 |
| `set_preference` | 保存用户偏好 |
| `get_system_info` | 获取系统信息 |
| `list_skills` | 列出所有可用技能 |

## 自定义 Skill 插件

在 `skills/` 目录下创建 `*_skill.py` 文件：

```python
from src.skills.registry import Skill, SkillRegistry

async def my_handler(**kwargs) -> str:
    return "Hello from my skill!"

def register(registry: SkillRegistry, agent=None) -> None:
    registry.register(Skill(
        name="my_skill",
        description="我的自定义技能",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "查询内容"},
            },
            "required": ["query"],
        },
        handler=my_handler,
    ))
```

Agent 启动时会自动加载该目录下的所有插件。

## 配置

编辑 `config/default.yaml` 或通过环境变量覆盖：

| 环境变量 | 说明 | 默认值 |
|----------|------|--------|
| `ZHIPU_API_KEY` | 智谱AI API密钥 | 必填 |
| `BF_CONFIG` | 配置文件路径 | `config/default.yaml` |

## 项目结构

```
best_friend/
├── src/
│   ├── agent/
│   │   ├── loop.py          # Agent 主循环
│   │   └── llm_client.py    # LLM 客户端
│   ├── memory/
│   │   ├── short_term.py    # 短期记忆
│   │   └── long_term.py     # 长期记忆 (SQLite)
│   ├── skills/
│   │   ├── registry.py      # Skill 注册中心
│   │   └── builtin/         # 内置 Skills
│   ├── scheduler/
│   │   └── scheduler.py     # 定时任务调度器
│   ├── types.py             # 类型定义
│   └── cli.py               # CLI 入口
├── config/default.yaml      # 默认配置
├── skills/                  # 自定义 Skill 插件目录
├── data/                    # 运行时数据 (SQLite)
├── run.sh                   # 一键运行脚本
└── pyproject.toml
```
