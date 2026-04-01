"""Skill加载系统 - 使用Progressive Disclosure实现"""

import os
import glob
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Any


@dataclass
class SkillMetadata:
    """技能元数据"""
    name: str
    category: str
    description: str
    keywords: List[str]
    use_cases: List[str]
    features: List[str]
    output_format: str
    notes: str
    path: Path
    full_content: Optional[str] = None


class SkillRegistry:
    """技能注册表 - 管理所有可用技能"""
    
    def __init__(self, skills_dir: str = "skills"):
        self.skills_dir = Path(skills_dir)
        self.skills: Dict[str, SkillMetadata] = {}
        self.loaded_skills: Dict[str, str] = {}
        self._discover_skills()
    
    def _discover_skills(self):
        """发现所有技能目录"""
        if not self.skills_dir.exists():
            return
        
        skill_dirs = glob.glob(str(self.skills_dir / "*/"))
        for skill_dir in skill_dirs:
            skill_path = Path(skill_dir)
            skill_md = skill_path / "SKILL.md"
            
            if skill_md.exists():
                try:
                    metadata = self._parse_skill_metadata(skill_md, skill_path)
                    if metadata:
                        self.skills[metadata.name] = metadata
                except Exception as e:
                    print(f"Warning: Failed to parse skill {skill_path}: {e}")
    
    def _parse_skill_metadata(self, skill_md: Path, skill_path: Path) -> Optional[SkillMetadata]:
        """解析技能元数据文件"""
        try:
            content = skill_md.read_text(encoding="utf-8").strip()
            
            # 解析各个字段
            category = ""
            description = ""
            keywords = []
            use_cases = []
            features = []
            output_format = ""
            notes = ""
            
            current_section = None
            for line in content.splitlines():
                line = line.strip()
                
                if line.startswith("## "):
                    current_section = line[3:].strip()
                    continue
                
                if current_section == "类别" and line:
                    category = line
                elif current_section == "描述" and line:
                    description = line
                elif current_section == "关键词" and line:
                    keywords = [k.strip() for k in line.split(",")]
                elif current_section == "使用场景" and line.startswith("- "):
                    use_cases.append(line[2:].strip())
                elif current_section == "功能特性" and line.startswith("- "):
                    features.append(line[2:].strip())
                elif current_section == "输出格式" and line:
                    output_format = line
                elif current_section == "注意事项" and line:
                    notes = line
            
            return SkillMetadata(
                name=skill_path.name,
                category=category,
                description=description,
                keywords=keywords,
                use_cases=use_cases,
                features=features,
                output_format=output_format,
                notes=notes,
                path=skill_path
            )
        except Exception as e:
            print(f"Error parsing skill metadata {skill_md}: {e}")
            return None
    
    def get_all_skills(self) -> List[SkillMetadata]:
        """获取所有技能的元数据"""
        return list(self.skills.values())
    
    def get_skill_by_name(self, name: str) -> Optional[SkillMetadata]:
        """根据名称获取技能"""
        return self.skills.get(name)
    
    def get_skills_by_category(self, category: str) -> List[SkillMetadata]:
        """根据类别获取技能"""
        return [skill for skill in self.skills.values() if skill.category == category]
    
    def get_skills_by_keyword(self, keyword: str) -> List[SkillMetadata]:
        """根据关键词搜索技能"""
        return [skill for skill in self.skills.values() if keyword.lower() in [k.lower() for k in skill.keywords]]
    
    def load_skill_content(self, skill_name: str) -> Optional[str]:
        """动态加载技能的完整内容"""
        if skill_name in self.loaded_skills:
            return self.loaded_skills[skill_name]
        
        skill = self.skills.get(skill_name)
        if not skill:
            return None
        
        try:
            # 读取技能目录下的所有文件
            all_content = []
            for file_path in skill.path.glob("*"):
                if file_path.is_file() and file_path.name != "SKILL.md":
                    try:
                        content = file_path.read_text(encoding="utf-8")
                        all_content.append(f"## {file_path.name}\n\n{content}")
                    except Exception as e:
                        all_content.append(f"## {file_path.name}\n\n[无法读取文件: {e}]")
            
            full_content = "\n\n".join(all_content)
            self.loaded_skills[skill_name] = full_content
            return full_content
        except Exception as e:
            print(f"Error loading skill content {skill_name}: {e}")
            return None
    
    def list_available_categories(self) -> List[str]:
        """列出所有可用的技能类别"""
        return list(set(skill.category for skill in self.skills.values() if skill.category))


# 全局技能注册表实例
_skill_registry: Optional[SkillRegistry] = None


def get_skill_registry() -> SkillRegistry:
    """获取全局技能注册表实例"""
    global _skill_registry
    if _skill_registry is None:
        _skill_registry = SkillRegistry()
    return _skill_registry


def build_skills_prompt() -> str:
    """构建技能系统提示词"""
    registry = get_skill_registry()
    skills = registry.get_all_skills()
    
    if not skills:
        return "# Skills 系统\n当前没有可用的技能。"
    
    prompt_parts = [
        "# Skills 系统",
        "本系统支持动态加载专业化技能，采用Progressive Disclosure机制。",
        "以下是所有可用技能的元数据："
    ]
    
    # 按类别组织技能
    categories = registry.list_available_categories()
    for category in categories:
        category_skills = registry.get_skills_by_category(category)
        
        prompt_parts.append(f"\n## {category} 类技能")
        
        for skill in category_skills:
            prompt_parts.extend([
                f"\n### {skill.name}",
                f"- **描述**: {skill.description}",
                f"- **关键词**: {', '.join(skill.keywords)}",
                f"- **使用场景**: {', '.join(skill.use_cases)}",
                f"- **功能特性**: {', '.join(skill.features)}",
                f"- **输出格式**: {skill.output_format}",
                f"- **注意事项**: {skill.notes}"
            ])
    
    prompt_parts.extend([
        "\n## 动态加载技能",
        "你可以使用 `load_skill` 工具来动态加载特定技能的完整内容。",
        "加载后可以获得该技能的详细实现、代码示例和配置模板。"
    ])
    
    return "\n".join(prompt_parts)