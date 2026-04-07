---
name: commit
description: 分析当前变更并创建 git commit
allowed-tools:
  - Bash(git add:*)
  - Bash(git status:*)
  - Bash(git diff:*)
  - Bash(git log:*)
  - Bash(git commit:*)
  - Read
  - Glob
  - Grep
argument-hint: "[commit message]"
when_to_use: "When the user asks to commit changes or create a git commit"
user-invocable: true
---

# Commit Skill

分析当前代码变更并创建一个 git commit。

## 当前状态

当前分支: !`git branch --show-current`
变更文件:
!`git diff --name-only`
近期提交: !`git log --oneline -10`

## 指令

1. 使用 `bash` 运行 `git status` 和 `git diff` 查看当前所有变更
2. 分析变更的性质和目的
3. 编写简洁的 commit message（中文或英文，与近期 commit 风格一致）
4. 暂存相关文件并执行 commit
5. 不要提交包含敏感信息的文件（.env, credentials 等）
