# 测试技能说明

这个技能用于测试和验证技能加载系统的功能。

## 功能

- 验证技能元数据解析
- 测试动态内容加载
- 演示Progressive Disclosure机制
- 提供系统调试信息

## 使用方法

1. 在系统提示词中查看技能元数据
2. 使用 `load_skill` 工具加载完整内容
3. 查看技能的实现细节

## 文件结构

- `SKILL.md` - 技能元数据定义
- `implementation.py` - 技能实现代码
- `README.md` - 技能说明文档

## 测试命令

```bash
cd skills/test-skill
python implementation.py
```