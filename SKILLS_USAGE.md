# Skills 系统使用指南

本系统实现了基于 Progressive Disclosure 的技能加载机制，让AI代理能够动态发现、搜索和加载专业技能。

## 🎯 核心概念

### Progressive Disclosure（渐进式披露）
- **元数据优先**：所有技能的元数据在系统启动时加载并集成到提示词中
- **按需加载**：只有在需要使用某个技能时才加载其完整内容
- **智能筛选**：支持按类别、关键词搜索技能
- **高效管理**：减少上下文窗口压力，提高响应效率

## 📁 技能目录结构

```
skills/
├── skill-name/              # 技能目录
│   ├── SKILL.md            # 技能元数据定义（必需）
│   ├── implementation.py   # 技能实现代码（可选）
│   ├── templates/          # 模板文件（可选）
│   └── README.md           # 技能说明文档（可选）
└── README.md               # 技能系统总说明
```

## 📝 SKILL.md 格式规范

```markdown
# 技能名称

## 描述
简短描述技能的功能和用途。

## 类别
技能所属类别，如：development, analysis, general 等。

## 关键词
用逗号分隔的关键词，用于搜索匹配。

## 使用场景
描述技能适用的具体场景。

## 功能特性
列出技能的主要功能和特性。

## 输出格式
描述技能输出的格式和内容。

## 注意事项
使用该技能时需要注意的事项。
```

## 🛠️ 可用工具

### 1. load_skill
动态加载特定技能的完整内容。

**参数：**
- `skill_name`: 技能名称（必需）

**示例：**
```python
load_skill.invoke({"skill_name": "test-skill"})
```

### 2. list_skills
列出可用技能，支持按类别筛选。

**参数：**
- `category`: 技能类别（可选）

**示例：**
```python
# 列出所有技能
list_skills.invoke({})

# 只列出 development 类别的技能
list_skills.invoke({"category": "development"})
```

### 3. search_skills
根据关键词搜索技能。

**参数：**
- `query`: 搜索关键词（必需）

**示例：**
```python
search_skills.invoke({"query": "langchain"})
```

## 🚀 使用示例

### 示例1：发现并使用技能

```python
# 1. 查看可用技能
skills = list_skills.invoke({})
print(skills)

# 2. 搜索特定技能
results = search_skills.invoke({"query": "langchain"})

# 3. 加载感兴趣的技能
content = load_skill.invoke({"skill_name": "langchain-project-setup"})
```

### 示例2：按类别浏览

```python
# 浏览开发类技能
dev_skills = list_skills.invoke({"category": "development"})
print(dev_skills)

# 浏览分析类技能
analysis_skills = list_skills.invoke({"category": "analysis"})
print(analysis_skills)
```

## 🔧 技能开发指南

### 创建新技能

1. **创建技能目录**
```bash
mkdir skills/my-new-skill
cd skills/my-new-skill
```

2. **创建 SKILL.md**
```markdown
# My New Skill

## 描述
这是一个新技能的描述。

## 类别
general

## 关键词
new, skill, example

## 使用场景
描述使用场景。

## 功能特性
- 特性1
- 特性2

## 输出格式
描述输出格式。

## 注意事项
注意事项说明。
```

3. **添加实现代码（可选）**
```python
# implementation.py
def execute_skill():
    """技能实现代码"""
    return "技能执行结果"
```

4. **添加文档（可选）**
```markdown
# README.md
技能的详细说明文档。
```

### 测试技能

```python
from src.skills import get_skill_registry

# 验证技能是否被正确发现
registry = get_skill_registry()
skill = registry.get_skill_by_name('my-new-skill')
print(skill.name, skill.description)

# 测试加载内容
content = registry.load_skill_content('my-new-skill')
print(content)
```

## 📊 当前可用技能

### Development 类
- `langchain-agent-patterns`: LangChain代理模式实现
- `langchain-project-setup`: LangChain项目快速搭建
- `test-skill`: 技能系统测试

### Analysis 类
- `langsmith-trace-analyzer`: LangSmith追踪数据分析

### General 类
- `example-skill`: 示例技能

## 🎨 系统集成

### 提示词集成
技能元数据自动集成到系统提示词中，格式如下：

```
# Skills 系统
本系统支持动态加载专业化技能，采用Progressive Disclosure机制。
以下是所有可用技能的元数据：

## 类别 技能

### 技能名称
- **描述**: 技能描述
- **关键词**: 关键词列表
- **使用场景**: 使用场景描述
...
```

### 工具注册
技能工具自动注册到LangChain工具系统中，可以通过以下方式访问：

```python
from src.skill_tool_registry import (
    load_skill_content,
    list_available_skills,
    search_skills_by_query
)
```

## 🔍 技能发现机制

系统会自动扫描 `skills/` 目录下的所有子目录，并查找 `SKILL.md` 文件。发现规则：

1. **目录扫描**：递归扫描 `skills/` 目录
2. **文件识别**：查找包含 `SKILL.md` 的目录
3. **元数据解析**：解析 `SKILL.md` 中的元数据
4. **内容缓存**：缓存元数据，按需加载完整内容
5. **去重处理**：确保技能名称唯一

## 💡 最佳实践

1. **技能命名**：使用清晰、描述性的技能名称
2. **关键词选择**：选择准确、相关的关键词便于搜索
3. **类别分类**：将技能归类到合适的类别
4. **文档完善**：提供详细的描述和使用说明
5. **代码质量**：保持实现代码的清晰和可维护性

## 🚨 注意事项

1. **SKILL.md 必需**：每个技能必须包含 `SKILL.md` 文件
2. **名称唯一性**：技能名称在系统中必须唯一
3. **内容大小**：建议单个技能内容不超过 4000 字符
4. **性能考虑**：大量技能可能影响系统启动时间
5. **版本控制**：技能内容变更应通过版本控制管理

## 📈 未来扩展

计划中的功能扩展：

1. **技能依赖管理**：支持技能之间的依赖关系
2. **技能版本控制**：支持技能的版本管理
3. **技能市场**：创建技能共享和分发平台
4. **自动更新**：支持技能的自动更新机制
5. **性能监控**：添加技能使用统计和性能监控

---

**注意**：本技能系统基于 LangChain 的 Skills Architecture 设计，参考了最新的 Agent 开发最佳实践。