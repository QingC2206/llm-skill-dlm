---
domain: "{领域编号}_{领域名}"
topic: "{topic_name}"
kind: "reference_topic"
reference_type: "official_doc"   # official_doc / api_doc / legacy_doc / spec / vendor_doc / internal_note
authority: "official"            # official / vendor / internal / historical_baseline
match_globs:
  - "src/path/to/*.cs"
  - "docs/vendor/topic/**/*.md"
status: "已归档"                  # 已归档 / 部分归档 / 待补充 / 已废弃
last_updated: "YYYY-MM-DD"
---

# {参考主题名称}

> **定位**: {这批权威/历史资料在本项目中的用途}
> **真相地位**: {例如“外部契约真相 / 历史基线 / 参考实现，不等于现行架构真相”}

## 1. 建议阅读顺序

定位未知时，通过本 README 确认资料边界与推荐入口，再按内容目录选择相关文档。已知准确来源时可以直接定位；需要精确合同、参数、错误码、版本差异或措辞时读取相应原文。

## 2. 与本项目的映射

- **适用任务/模块**: {哪些模块或任务会引用这批资料}
- **主要回答的问题**: {这批资料能回答什么，不能回答什么}
- **与 L2 的关系**: L2 只记录“这批资料对本项目意味着什么”；原始事实仍回到本 topic 下的具体资料。

## 3. 内容目录

| 文档 | 类型 | 价值 | 何时需要下钻 |
|------|------|------|--------------|
| `{path/to/doc1.md}` | API / Official / Legacy | {摘要} | {何时需要读原文} |
| `{path/to/doc2.md}` | Spec / Guide | {摘要} | {何时需要读原文} |

## 4. 阅读与使用边界

- 这批资料保留近原文事实，不强制改写成 L2 风格。
- 若原文与当前代码/L2 冲突：外部合同类事实以权威原文为准，项目内实现现状以源码为准，并在 L2 中标注差异。
- 若后续只补充原文，不应顺手重写 L2；只有形成新的项目内隐性结论时才回写 L2。

## 5. 来源与维护

- **来源**: {官方站点 / 仓库 / legacy 路径 / 导入日期}
- **收录范围**: {为何只收这部分，不收全量}
- **维护规则**:
  - topic README 是该主题唯一可路由入口
  - raw docs 只作为具体权威资料，不直接进入 L1 路由表
  - 新增资料时，优先补 `## 3. 内容目录` 与本 README 的阅读顺序说明
