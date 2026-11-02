# {领域名} 路由 (L1)

> **定位**: {领域名} 的宏观骨架 + L2 导航路由
> **更新日期**: YYYY-MM-DD

---

## 一、源码目录树

<!-- 目录树仅在有助于定位 owner 时保留，可改为准确入口说明，也可整体省略本节。路由标题的编号可选，同步脚本按实际章节调整已有编号。 -->

```text
{root_path}/
├── {dir1}/                  ← {注释}
│   ├── {file1}              ← {注释}
│   └── {file2}              ← {注释}
├── {dir2}/                  ← {注释}
└── {dir3}/                  ← {注释}
```

---

## 二、L2 路由表

> 本表聚合了各 L2 的 YAML front-matter, 是文件→L2 的**编译索引**。
> 做路径映射或知识回写时, 优先查本表, 无需逐个打开 L2。
> 模板中的占位行仅用于首版脚手架; 正式维护时应运行 `dlm kb route` 重编译本表, 手动修改会在下次同步时被覆盖。

| 当你需要了解... | 去读 | match_globs | 状态 |
|---|---|---|:---:|
| {概念/模块1} | `L2_Modules/{module1}.md` | `src/handlers/*.hpp` | 已实现 |
| {概念/模块2} | `L2_Modules/{module2}.md` | `src/core/*.cxx` | 已实现 |
| {概念/模块3} | `L2_Modules/{module3}.md` | `include/*.h` | 待实现 |

> **维护规则**: 新增/删除 L2 后, 运行 `dlm kb route` 重编译本表; 不要把占位行当成人工长期维护格式。

---

## 三、参考资料路由

> 本表聚合了 `references/*/README.md` 的 YAML front-matter, 是文件→reference topic README 的**编译索引**。
> 只把 topic README 作为入口; topic 内 raw docs 在执行阶段按目录卡继续下钻。
> 若当前领域暂无 Domain 级 reference topic, 本段可整体省略; `dlm kb route` 在无 topic 时也会自动删除该段。
> 模板中的占位行仅用于首版脚手架; 正式维护时应运行 `dlm kb route` 重编译本表, 手动修改会在下次同步时被覆盖。

| 当你需要查阅... | 去读 | 类型 | 权威性 | match_globs | 状态 |
|---|---|---|---|---|:---:|
| {参考主题1} | `references/{topic1}/README.md` | official_doc | official | `src/contracts/*.json` | 已归档 |
| {参考主题2} | `references/{topic2}/README.md` | legacy_doc | historical_baseline | `legacy/docs/**/*.md` | 已归档 |

> **维护规则**: 新增/删除 Domain 级参考主题后, 运行 `dlm kb route` 重编译本表。
> raw docs 不直接进入本表; topic README 才是人类和 LLM 的稳定入口。

---

## 四、跨模块交互关系

> 描述本领域内模块之间的协作拓扑, 以及与外部领域的接口边界。
> 使用 Mermaid 图或文字描述均可。

```mermaid
graph LR
    A[{模块A}] --> B[{模块B}]
    B --> C[{外部领域接口}]
```

> L1 负责导航与必要的模块关系，签名和实现细节通过源码或 Reference 定位。图和目录按导航需要保留。
