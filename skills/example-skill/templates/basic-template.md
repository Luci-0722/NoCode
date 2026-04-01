# 基础技能模板

## 技能结构
```
skills/skill-name/
├── SKILL.md              # 技能定义和文档
├── implementation.py     # 实现代码
├── templates/           # 模板文件
│   └── basic-template.md
└── references.md        # 参考资料
```

## SKILL.md 模板
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

## implementation.py 模板
```python
"""技能实现"""

from typing import Dict, List, Optional

def skill_function(input_data: str) -> str:
    """技能函数"""
    return f"处理输入: {input_data}"

def get_skill_info() -> Dict:
    """获取技能信息"""
    return {
        "name": "skill_name",
        "version": "1.0.0",
        "description": "技能描述",
        "functions": ["skill_function"]
    }
```

## 使用方法
1. 在 `skills/` 目录下创建技能文件夹
2. 按照模板创建必要的文件
3. 实现具体的技能功能
4. 测试技能功能
5. 集成到技能系统中

## 最佳实践
- 保持技能的专注性和单一职责
- 提供清晰的文档和使用说明
- 包含完整的错误处理
- 支持配置参数
- 提供测试用例