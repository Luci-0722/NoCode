"""技能工具注册表"""

from langchain_core.tools import tool
from .skill_tools import load_skill, list_skills, search_skills


@tool
def load_skill_content(skill_name: str) -> str:
    """
    动态加载特定技能的完整内容
    
    Args:
        skill_name: 要加载的技能名称
        
    Returns:
        技能的完整内容，包括实现代码、模板和参考资料
    """
    result = load_skill(skill_name)
    
    if not result["success"]:
        return f"错误: {result['error']}\n\n可用技能: {', '.join(result.get('available_skills', []))}"
    
    # 格式化输出
    output = f"""# 技能: {result['skill_name']}

## 基本信息
- **类别**: {result['category']}
- **描述**: {result['description']}
- **关键词**: {', '.join(result['keywords'])}

## 使用场景
{chr(10).join(f"- {use_case}" for use_case in result['use_cases'])}

## 功能特性
{chr(10).join(f"- {feature}" for feature in result['features'])}

## 输出格式
{result['output_format']}

## 注意事项
{result['notes']}

## 完整内容
{result['full_content']}
"""
    return output


@tool
def list_available_skills(category: str = None, keyword: str = None) -> str:
    """
    列出所有可用的技能
    
    Args:
        category: 可选，按类别筛选技能
        keyword: 可选，按关键词筛选技能
        
    Returns:
        可用技能的列表信息
    """
    result = list_skills(category, keyword)
    
    output = f"""# 可用技能列表

## 统计信息
- 总计技能数: {result['total_count']}
- 可用类别: {', '.join(result['categories'])}

"""
    
    if result['skills']:
        output += "## 技能详情\n\n"
        for skill in result['skills']:
            output += f"""### {skill['name']}
- **类别**: {skill['category']}
- **描述**: {skill['description']}
- **关键词**: {', '.join(skill['keywords'])}
- **使用场景**: {', '.join(skill['use_cases'])}

"""
    else:
        output += "没有找到匹配的技能。\n"
    
    return output


@tool
def search_skills_by_query(query: str) -> str:
    """
    搜索技能
    
    Args:
        query: 搜索查询字符串
        
    Returns:
        匹配的技能列表
    """
    result = search_skills(query)
    
    output = f"""# 技能搜索结果

## 搜索查询
"{result['query']}"

## 匹配结果
找到 {result['total_matches']} 个匹配技能

"""
    
    if result['skills']:
        output += "## 匹配技能\n\n"
        for skill in result['skills']:
            output += f"""### {skill['name']}
- **类别**: {skill['category']}
- **描述**: {skill['description']}
- **关键词**: {', '.join(skill['keywords'])}
- **使用场景**: {', '.join(skill['use_cases'])}

"""
    else:
        output += "没有找到匹配的技能。\n"
    
    return output


# 所有技能工具的列表
SKILL_TOOLS = [
    load_skill_content,
    list_available_skills,
    search_skills_by_query
]