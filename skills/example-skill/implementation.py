"""示例技能实现"""

from typing import Dict, List, Optional


def example_skill_function(input_data: str) -> str:
    """示例技能函数"""
    return f"处理输入: {input_data} - 这是示例技能的输出"


def get_skill_info() -> Dict:
    """获取技能信息"""
    return {
        "name": "example_skill",
        "version": "1.0.0",
        "description": "示例技能",
        "author": "Skill System",
        "created": "2024-01-01",
        "functions": [
            "example_skill_function"
        ]
    }


def demonstrate_progressive_disclosure() -> Dict:
    """演示渐进式披露机制"""
    return {
        "metadata": {
            "name": "example_skill",
            "description": "这是一个示例技能，展示了渐进式披露机制",
            "category": "general",
            "keywords": ["example", "demo", "template"]
        },
        "implementation": """
def example_skill_function(input_data: str) -> str:
    \"\"\"示例技能函数\"\"\"
    return f"处理输入: {input_data} - 这是示例技能的输出"
""",
        "usage": """
# 使用示例
result = example_skill_function("Hello, World!")
print(result)  # 输出: 处理输入: Hello, World! - 这是示例技能的输出
""",
        "benefits": [
            "轻量级技能定义",
            "渐进式内容披露",
            "易于扩展和维护",
            "支持动态加载"
        ]
    }


# 使用示例
if __name__ == "__main__":
    # 演示技能功能
    print("=== 示例技能演示 ===")
    
    # 获取技能信息
    info = get_skill_info()
    print(f"技能名称: {info['name']}")
    print(f"技能版本: {info['version']}")
    print(f"技能描述: {info['description']}")
    print()
    
    # 演示技能函数
    result = example_skill_function("测试数据")
    print(f"技能函数输出: {result}")
    print()
    
    # 演示渐进式披露
    disclosure = demonstrate_progressive_disclosure()
    print("=== 渐进式披露演示 ===")
    print(f"技能元数据: {disclosure['metadata']}")
    print("实现代码:")
    print(disclosure['implementation'])
    print("使用方法:")
    print(disclosure['usage'])
    print("优势:")
    for benefit in disclosure['benefits']:
        print(f"- {benefit}")
'''