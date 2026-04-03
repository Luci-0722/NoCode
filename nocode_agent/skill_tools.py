"""技能加载工具"""

from typing import Dict, Any
from .skills import get_skill_registry


def load_skill(skill_name: str) -> Dict[str, Any]:
    """
    动态加载技能的完整内容
    
    Args:
        skill_name: 技能名称
        
    Returns:
        包含技能完整信息的字典
    """
    registry = get_skill_registry()
    
    # 获取技能元数据
    skill_metadata = registry.get_skill_by_name(skill_name)
    if not skill_metadata:
        return {
            "success": False,
            "error": f"未找到技能: {skill_name}",
            "available_skills": [s.name for s in registry.get_all_skills()]
        }
    
    # 加载技能完整内容
    full_content = registry.load_skill_content(skill_name)
    
    if full_content is None:
        return {
            "success": False,
            "error": f"无法加载技能内容: {skill_name}",
            "metadata": {
                "name": skill_metadata.name,
                "category": skill_metadata.category,
                "description": skill_metadata.description
            }
        }
    
    return {
        "success": True,
        "skill_name": skill_metadata.name,
        "category": skill_metadata.category,
        "description": skill_metadata.description,
        "keywords": skill_metadata.keywords,
        "use_cases": skill_metadata.use_cases,
        "features": skill_metadata.features,
        "output_format": skill_metadata.output_format,
        "notes": skill_metadata.notes,
        "full_content": full_content,
        "metadata_only": False
    }


def list_skills(category: str = None, keyword: str = None) -> Dict[str, Any]:
    """
    列出可用技能
    
    Args:
        category: 可选，按类别筛选
        keyword: 可选，按关键词筛选
        
    Returns:
        技能列表信息
    """
    registry = get_skill_registry()
    
    skills = registry.get_all_skills()
    
    # 应用筛选
    if category:
        skills = registry.get_skills_by_category(category)
    elif keyword:
        skills = registry.get_skills_by_keyword(keyword)
    
    return {
        "total_count": len(skills),
        "categories": registry.list_available_categories(),
        "skills": [
            {
                "name": skill.name,
                "category": skill.category,
                "description": skill.description,
                "keywords": skill.keywords,
                "use_cases": skill.use_cases[:3]  # 只显示前3个使用场景
            }
            for skill in skills
        ]
    }


def search_skills(query: str) -> Dict[str, Any]:
    """
    搜索技能
    
    Args:
        query: 搜索查询字符串
        
    Returns:
        匹配的技能列表
    """
    registry = get_skill_registry()
    skills = registry.get_all_skills()
    
    # 简单的文本搜索
    query_lower = query.lower()
    matching_skills = []
    
    for skill in skills:
        if (query_lower in skill.name.lower() or 
            query_lower in skill.description.lower() or
            any(query_lower in keyword.lower() for keyword in skill.keywords) or
            any(query_lower in use_case.lower() for use_case in skill.use_cases)):
            matching_skills.append(skill)
    
    return {
        "query": query,
        "total_matches": len(matching_skills),
        "skills": [
            {
                "name": skill.name,
                "category": skill.category,
                "description": skill.description,
                "keywords": skill.keywords,
                "use_cases": skill.use_cases[:3]
            }
            for skill in matching_skills
        ]
    }