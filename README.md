# llm-skill-dlm

可集成到现有仓库的 DLM 技能包，提供任务管理、全集盘点、证据验收、知识回写和共享租约队列的完整闭环。运行依赖为 **Python 3.10+ 标准库**；Git 用于识别工作区变更，无需安装 Python 第三方包。

## 集成到仓库

先取得本仓库，然后对需要使用 DLM 的仓库执行安装：

```powershell
git clone https://github.com/QingC2206/llm-skill-dlm.git
python -X utf8 llm-skill-dlm/install.py --host "你的项目路径"
```

安装器将 `skills/` 中的五个技能及 `_shared/` 一起复制到宿主的 `.agents/skills/`。首次安装在宿主没有配置时创建 `dlm.config.json`：新项目默认使用 `docs-llm/`，已有 `doc_llm/` 或 `docs_llm/` 会自动沿用。

安装后进入宿主根目录运行：

```powershell
python -X utf8 .agents/skills/dlm-task/scripts/dlm.py --help
python -X utf8 .agents/skills/dlm-knowledge/scripts/dlm.py kb scaffold init-project "项目名称"
```

第二条命令适用于需要创建知识库骨架的项目；生成后按实际事实填写内容。已有知识库直接继续使用，无需重新生成。支持 `.agents/skills/` 的 Agent 可发现 `$dlm-task`、`$dlm-load`、`$dlm-review`、`$dlm-knowledge` 和 `$dlm-multi-llm-lease`；其他 Agent 可按下表直接读取对应 `SKILL.md` 并调用相同 CLI。

安装保留宿主的 `AGENTS.md`、已有配置、门禁策略、任务账本和知识文档。技能包可以随宿主一起提交；安装后运行不依赖原克隆目录、绝对路径或符号链接。

更新本技能包后，重新运行安装命令即可更新宿主。安装器在 `.agents/dlm-install.json` 中记录文件摘要，仅更新未被本地修改的受管文件；发生内容冲突时，在写入前报告具体文件。与当前包逐字节一致的旧版手工副本可以直接纳入管理。不要只复制单个技能目录，五个技能共享 `_shared/`，盘点与租约队列之间也存在调用关系。

```powershell
python -X utf8 llm-skill-dlm/install.py --host "你的项目路径" --dry-run
python -X utf8 llm-skill-dlm/install.py --host "你的项目路径" --check
```

`--dry-run` 只预览；`--check` 校验已安装内容与当前包是否一致。两者均不写文件。

也可将本仓库作为 Git submodule 放到宿主自选位置，再运行其中的 `install.py --host ...`。升级子模块版本后重新安装，宿主内的技能副本便与该版本同步。

## 完整闭环

| 入口 | 能力 |
|---|---|
| [dlm-task](skills/dlm-task/SKILL.md) | 用户确认原则后创建 Epic；登记义务、写集、决议和证据；启动、续接、checkpoint、逐项验收、关闭、转交与重开 |
| [dlm-load](skills/dlm-load/SKILL.md) | 渲染任务简报，按当前问题加载 owner 知识，承接上一轮未解决事项 |
| [dlm-review](skills/dlm-review/SKILL.md) | 按用户目标和实际证据复审，完整复审先枚举对象全集 |
| [dlm-knowledge](skills/dlm-knowledge/SKILL.md) | BRIEF、L0/L1/L2、Reference、SOP 的生成、路由、校验、过期候选和知识回写 |
| [dlm-multi-llm-lease](skills/dlm-multi-llm-lease/SKILL.md) | 用户选用共享队列时，领取、心跳、完成、回收、重开以及盘点导入/回填 |

任务状态由 `ledger.json` 驱动，README 和 backlog 是生成视图。典型流程为：确认原则 → 创建 Epic / 义务 / Subtask → `plan-check` → `task start` → 实施与证据 → `checkpoint` → `close` → `epic close`。有复用价值的发现通过知识技能写回并更新路由。`close` 只证明机械条件满足，执行者仍需判断实际结果是否达到目标。

覆盖型工作通过 `inv source` 从事实来源枚举全集，再逐项登记结果；未验证项不会因为命令成功或编译通过而自动变为通过。共享队列可通过 `inv export-lease` 与 `inv sync-lease` 接入同一份全集。

技能包包含授权边界和完成规则，但不会替代宿主的授权规则。创建真实 Epic 仍须先取得用户对具体原则的确认；安装本包不会创建任务、作出用户决议或启动并行执行者。

详见 [CLI 命令参考](skills/_shared/references/cli.md)、[账本与约束](skills/_shared/references/ledger.md)、[宿主集成与策略配置](skills/_shared/references/host-integration.md)。

## 直接调用与宿主配置

只需要 CLI 时，也可在本仓库根目录运行并显式指定宿主：

```powershell
python -X utf8 dlm.py --root "你的项目路径" kb scaffold init-project "项目名称"
python -X utf8 dlm.py --root "你的项目路径" check
```

`--root` 必须放在子命令前，路径需为已存在的目录。省略它时沿用 DLM 原有的宿主发现规则：`DLM_PROJECT_ROOT` → 从当前目录向上查找 `dlm.config.json` → 查找已知知识目录。运行已安装的技能时无需设置环境变量。

可选的宿主 `dlm.config.json`：

```json
{
  "knowledge_dir": "docs-llm",
  "tasks_dir": "docs-llm/Tasks",
  "cache_dir": "out",
  "locale": "zh-CN",
  "policy": "docs-llm/dlm.policy.json"
}
```

`dlm.policy.json` 可声明宿主公共层、路径触发的验证门禁及规划提示阈值。缺省不附带任何项目专属门禁；门禁命令用于说明要求，实际执行和证据登记由执行者完成。Git 不可用时，收口需显式提供 `--changed` 说明实际变更范围。

知识与任务数据属于宿主，不存放在本技能仓库中。技能提示与参考文档使用英文；知识模板和默认任务输出使用中文。已有 v1 任务支持只读检查，需要续接时通过 `adopt` 导入。

## 验证与维护

```powershell
python -X utf8 scripts/test_all.py
```

此入口为原有测试提供隔离宿主，执行全部共享运行时测试、租约测试及安装集成测试。集成测试覆盖新仓库安装、五个技能入口、默认与自定义知识目录、任务闭环和知识回写、重复安装、更新及冲突保护。新增集成测试的宿主位于 `out/`；原有测试沿用自动清理的系统临时目录，以验证没有 Git 父仓库时的行为。测试不会写入来源仓库。

仓库内 `skills/` 保留来源技能包的完整布局，包含全部运行脚本、参考资料、模板、语言资源及测试。维护方法见 [技能包治理](skills/_shared/references/dlm_skill_pack_governance.md)，独立仓库的集成变更见 [CHANGELOG.md](CHANGELOG.md)。
