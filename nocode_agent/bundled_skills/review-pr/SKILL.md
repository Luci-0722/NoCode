---
name: review-pr
description: 审查当前分支的代码变更，生成 PR 审查报告
allowed-tools:
  - Bash(git log:*)
  - Bash(git diff:*)
  - Read
  - Glob
  - Grep
argument-hint: "[base-branch]"
arguments:
  - base_branch
when_to_use: "When the user asks to review a PR or review code changes"
user-invocable: true
---

# Review PR Skill

审查当前分支相对于基准分支的代码变更。

## 当前状态

当前分支: !`git branch --show-current`
目标分支: $base_branch
变更统计:
!`git diff --stat $base_branch...HEAD 2>/dev/null || git diff --stat main...HEAD`

## 指令

1. 使用 `git diff` 查看当前分支相对于基准分支的所有变更
2. 逐文件阅读关键变更
3. 检查以下问题：
   - 逻辑错误或潜在的 bug
   - 安全漏洞（SQL 注入、XSS 等）
   - 性能问题
   - 代码风格和可维护性
4. 生成结构化的审查报告，按严重程度分类
