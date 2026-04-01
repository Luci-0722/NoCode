#!/usr/bin/env python3
"""
技能系统完整性测试脚本
验证技能加载、工具集成和提示词生成的完整性
"""

import sys
from pathlib import Path

# 添加项目路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

def test_skill_discovery():
    """测试技能发现功能"""
    print("🔍 测试技能发现...")
    from src.skills import get_skill_registry
    
    registry = get_skill_registry()
    skills = registry.get_all_skills()
    
    assert len(skills) > 0, "未发现任何技能"
    print(f"✓ 发现 {len(skills)} 个技能")
    
    # 测试技能元数据
    for skill in skills:
        assert skill.name, f"技能 {skill} 缺少名称"
        assert skill.description, f"技能 {skill.name} 缺少描述"
        assert skill.category, f"技能 {skill.name} 缺少类别"
    
    print("✓ 所有技能元数据完整")
    return True

def test_skill_search():
    """测试技能搜索功能"""
    print("\n🔍 测试技能搜索...")
    from src.skills import get_skill_registry
    
    registry = get_skill_registry()
    
    # 测试按类别搜索
    dev_skills = registry.get_skills_by_category("development")
    assert len(dev_skills) > 0, "未找到 development 类别的技能"
    print(f"✓ 找到 {len(dev_skills)} 个 development 类技能")
    
    # 测试按关键词搜索
    langchain_skills = registry.get_skills_by_keyword("langchain")
    assert len(langchain_skills) > 0, "未找到包含 'langchain' 的技能"
    print(f"✓ 找到 {len(langchain_skills)} 个包含 'langchain' 的技能")
    
    return True

def test_skill_loading():
    """测试技能动态加载"""
    print("\n🔍 测试技能动态加载...")
    from src.skills import get_skill_registry
    
    registry = get_skill_registry()
    
    # 测试加载存在的技能
    content = registry.load_skill_content("test-skill")
    assert content is not None, "技能内容为空"
    assert len(content) > 0, "技能内容为空"
    print(f"✓ 成功加载 test-skill，内容长度: {len(content)} 字符")
    
    # 测试加载不存在的技能
    content = registry.load_skill_content("nonexistent-skill")
    assert content is None, "应该返回 None 对于不存在的技能"
    print(f"✓ 正确处理不存在的技能: 返回 None")
    
    return True

def test_tool_integration():
    """测试工具集成"""
    print("\n🔍 测试工具集成...")
    from src.skill_tool_registry import (
        load_skill_content,
        list_available_skills,
        search_skills_by_query
    )
    
    # 测试列出技能
    result = list_available_skills.invoke({})
    assert len(result) > 0, "列出技能结果为空"
    assert "可用技能列表" in result, "结果格式不正确"
    print("✓ list_available_skills 工具正常工作")
    
    # 测试按类别筛选
    result = list_available_skills.invoke({"category": "development"})
    assert "development" in result, "类别筛选结果不正确"
    print("✓ 类别筛选功能正常工作")
    
    # 测试搜索技能
    result = search_skills_by_query.invoke({"query": "langchain"})
    assert "匹配结果" in result, "搜索结果格式不正确"
    print("✓ search_skills_by_query 工具正常工作")
    
    # 测试加载技能内容
    result = load_skill_content.invoke({"skill_name": "test-skill"})
    assert "test-skill" in result, "加载内容不正确"
    print("✓ load_skill_content 工具正常工作")
    
    return True

def test_prompt_integration():
    """测试提示词集成"""
    print("\n🔍 测试提示词集成...")
    from src.prompts import build_main_system_prompt
    
    prompt = build_main_system_prompt()
    
    # 检查是否包含技能系统部分
    assert "# Skills 系统" in prompt, "提示词中未包含技能系统"
    print("✓ 提示词包含技能系统部分")
    
    # 检查是否包含技能元数据
    assert "langchain-agent-patterns" in prompt, "提示词中未包含技能元数据"
    print("✓ 提示词包含技能元数据")
    
    # 检查是否包含使用说明
    assert "load_skill" in prompt, "提示词中未包含工具使用说明"
    print("✓ 提示词包含工具使用说明")
    
    print(f"✓ 提示词总长度: {len(prompt)} 字符")
    return True

def test_progressive_disclosure():
    """测试 Progressive Disclosure 机制"""
    print("\n🔍 测试 Progressive Disclosure 机制...")
    from src.skills import get_skill_registry
    
    registry = get_skill_registry()
    
    # 检查元数据是否已加载
    skills = registry.get_all_skills()
    assert len(skills) > 0, "元数据未加载"
    print("✓ 元数据已预加载")
    
    # 检查内容是否按需加载
    # 这里我们假设内容缓存是空的，然后加载一个技能
    initial_cache_size = len(registry.loaded_skills)
    content = registry.load_skill_content("test-skill")
    final_cache_size = len(registry.loaded_skills)
    
    assert final_cache_size >= initial_cache_size, "内容缓存未更新"
    print(f"✓ 内容按需加载，缓存从 {initial_cache_size} 增加到 {final_cache_size}")
    
    # 验证缓存确实包含加载的技能
    assert "test-skill" in registry.loaded_skills, "技能未正确缓存"
    print("✓ 技能正确缓存在内存中")
    
    return True

def run_all_tests():
    """运行所有测试"""
    print("🚀 开始技能系统完整性测试\n")
    print("=" * 50)
    
    tests = [
        test_skill_discovery,
        test_skill_search,
        test_skill_loading,
        test_tool_integration,
        test_prompt_integration,
        test_progressive_disclosure
    ]
    
    results = []
    for test in tests:
        try:
            result = test()
            results.append((test.__name__, result, None))
        except Exception as e:
            results.append((test.__name__, False, str(e)))
            print(f"✗ {test.__name__} 失败: {e}")
    
    print("\n" + "=" * 50)
    print("📊 测试结果总结:")
    
    passed = sum(1 for _, result, _ in results if result)
    total = len(results)
    
    for name, result, error in results:
        status = "✓ 通过" if result else f"✗ 失败: {error}"
        print(f"  {name}: {status}")
    
    print(f"\n总计: {passed}/{total} 测试通过")
    
    if passed == total:
        print("\n🎉 所有测试通过！技能系统运行正常。")
        return 0
    else:
        print(f"\n⚠️  {total - passed} 个测试失败，请检查系统配置。")
        return 1

if __name__ == "__main__":
    exit_code = run_all_tests()
    sys.exit(exit_code)