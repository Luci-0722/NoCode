# Skills 系统

这是一个基于Progressive Disclosure机制的技能系统，用于管理和组织AI代理的专业技能。

## 系统概述

Skills系统采用渐进式披露（Progressive Disclosure）架构，实现了以下特性：

### 🎯 核心特性
- **元数据预加载**: 所有技能的元信息（名称、描述、类别、关键词）预加载到系统提示词中
- **按需加载**: 根据任务需求动态加载技能的完整内容
- **模块化设计**: 每个技能都是独立的模块，易于维护和扩展
- **标准化结构**: 统一的技能目录结构和文件格式

### 🏗️ 系统架构
```
skills/
├── langchain-project-setup/     # LangChain项目搭建技能
├── langchain-agent-patterns/    # LangChain代理模式技能
├── langsmith-trace-analyzer/   # LangSmith追踪分析技能
├── example-skill/              # 示例技能
└── README.md                   # 本文件
```

## 技能结构

每个技能都遵循标准化的目录结构：

```
skills/skill-name/
├── SKILL.md              # 技能定义和文档
├── implementation.py     # 实现代码
├── templates/           # 模板文件
├── references.md        # 参考资料
└── (其他自定义文件)
```

### SKILL.md 文件格式
```markdown
# 技能名称

## 类别
development

## 描述
技能的详细描述...

## 关键词
keyword1, keyword2, keyword3

## 使用场景
- 场景1
- 场景2
- 场景3

## 功能特性
- 特性1
- 特性2
- 特性3

## 输出格式
描述输出的格式和结构...

## 注意事项
- 注意事项1
- 注意事项2
```

## 使用方法

### 1. 查看可用技能
技能元数据会自动加载到系统提示词中，你可以看到所有可用的技能列表。

### 2. 搜索技能
使用 `search_skills` 工具根据关键词搜索相关技能：

```
search_skills "langchain"
```

### 3. 加载技能内容
使用 `load_skill` 工具动态加载特定技能的完整内容：

```
load_skill "langchain-project-setup"
```

### 4. 使用技能功能
加载技能后，你可以使用该技能提供的具体功能。

## 可用技能

### 1. LangChain项目搭建 (`langchain-project-setup`)
- **类别**: development
- **描述**: 专门用于快速搭建LangChain项目
- **功能**: 自动生成项目结构、配置文件、实现代码等
- **使用场景**: 新建LangChain项目时快速搭建基础结构

### 2. LangChain代理模式 (`langchain-agent-patterns`)
- **类别**: development  
- **描述**: 理解和实现LangChain代理模式
- **功能**: 代理模式分类、架构设计、实现模板等
- **使用场景**: 设计和实现LangChain代理架构

### 3. LangSmith追踪分析 (`langsmith-trace-analyzer`)
- **类别**: analysis
- **描述**: 分析和优化LangSmith追踪数据
- **功能**: 性能分析、错误诊断、优化建议等
- **使用场景**: 分析LangChain代理执行性能

### 4. 示例技能 (`example-skill`)
- **类别**: general
- **描述**: 展示技能系统基本结构
- **功能**: 基础技能功能演示
- **使用场景**: 学习技能系统结构、作为模板使用

## 开发新技能

### 1. 创建技能目录
```bash
mkdir -p skills/new-skill-name
```

### 2. 创建技能定义文件
```markdown
# 新技能名称

## 类别
development

## 描述
技能的详细描述...

## 关键词
keyword1, keyword2, keyword3

## 使用场景
- 场景1
- 场景2
- 场景3

## 功能特性
- 特性1
- 特性2
- 特性3

## 输出格式
描述输出的格式和结构...

## 注意事项
- 注意事项1
- 注意事项2
```

### 3. 实现技能功能
```python
"""技能实现"""

from typing import Dict, List, Optional

def skill_function(input_data: str) -> str:
    """技能函数"""
    return f"处理输入: {input_data}"

def get_skill_info() -> Dict:
    """获取技能信息"""
    return {
        "name": "new_skill",
        "version": "1.0.0",
        "description": "新技能描述",
        "functions": ["skill_function"]
    }
```

### 4. 添加模板和参考资料
在 `templates/` 和 `references.md` 中添加相关内容。

### 5. 测试技能
```bash
# 测试技能发现
python -c "from src.skills import skill_registry; print(skill_registry.discover_skills())"

# 测试技能加载
python -c "from src.skills import skill_registry; content = skill_registry.load_skill_content('new-skill-name'); print(content)"
```

## 技能分类

系统支持以下技能分类：

- **general**: 通用技能
- **development**: 开发相关
- **testing**: 测试相关  
- **deployment**: 部署相关
- **analysis**: 分析相关
- **documentation**: 文档相关

## 最佳实践

### 1. 技能设计原则
- **单一职责**: 每个技能专注于特定领域
- **标准化**: 遵循统一的目录结构和文件格式
- **可重用**: 设计可重用的功能模块
- **可扩展**: 支持功能扩展和配置

### 2. 文档规范
- **清晰描述**: 提供详细的技能描述
- **使用场景**: 明确技能的适用场景
- **功能特性**: 列出主要功能特性
- **注意事项**: 提供重要的使用注意事项

### 3. 代码质量
- **错误处理**: 完整的错误处理机制
- **类型提示**: 使用TypeScript类型提示
- **文档字符串**: 提供详细的函数文档
- **测试覆盖**: 包含完整的测试用例

## 系统配置

### 1. 技能目录配置
```python
from src.skills import SkillRegistry

# 自定义技能目录
registry = SkillRegistry(skills_dir="custom-skills")
```

### 2. 性能优化
- **缓存机制**: 技能内容缓存，避免重复加载
- **懒加载**: 按需加载技能内容
- **内存管理**: 合理管理内存使用

## 故障排除

### 1. 技能发现问题
- 检查技能目录是否存在
- 确认SKILL.md文件格式正确
- 验证文件权限设置

### 2. 技能加载问题
- 检查实现文件是否存在
- 确认代码语法正确
- 查看错误日志信息

### 3. 系统集成问题
- 确认技能工具已正确注册
- 检查系统提示词是否包含技能信息
- 验证代理配置是否正确

## 贡献指南

欢迎贡献新的技能！请遵循以下步骤：

1. Fork项目仓库
2. 创建新的技能分支
3. 按照规范创建新技能
4. 提交Pull Request
5. 等待代码审查

## 许可证

本项目采用MIT许可证，详见LICENSE文件。