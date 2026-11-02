---
kind: "sop"
id: "{sop_id}"
title: "{SOP 标题}"
owner_domain: "{owner_domain}"   # global / Domains/{N}_{domain}
status: "active"                 # active / draft / deprecated
last_updated: YYYY-MM-DD
activation_aliases:
  - "{触发别名}"
match_globs:
  - "{glob}"
validator_commands:
  - "{校验命令}"
---

# {SOP 标题}

> 保留 front-matter、`SOPs/{sop}/README.md` 入口和必要路由字段。正文按实际场景组织，保留目标、必要约束和完成条件；以下内容提示按需要选用，无需填齐固定章节。

## 适用场景

- {哪些场景需要这份方法}
- {什么时候不需要加载}

## 目标

- {这份 SOP 保证什么结果}
- {它不保证什么结果}

## 前置阅读

- {当前场景需要但执行者尚未掌握的知识及入口；没有时省略}

## 操作流程

{场景方法与关键判断；仅在顺序影响正确性时规定固定步骤}

## 硬约束

- {场景内必须遵守的约束}

## 推荐模式

- {推荐的实现模式、资源入口或已有范式}

## 自查清单

{足以支持完成判断的观测或检查；采用适合本场景的形式}

## 校验命令

```bash
{校验命令}
```

## 允许例外

- {什么情况下可以例外}
- {例外必须如何记录}
