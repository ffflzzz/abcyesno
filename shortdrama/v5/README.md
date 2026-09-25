# v5 —— 自包含包（supervisor 架构 · 唯一创作链）

**本包是仓库唯一活跃代码。** `new_deep_agent/`（fork 来源）与根 `langgraph.json` 已于
commit `9c60373`（2026-09-12）删除——v5 不再有"对端"，亦无需维持边界约束。

## 包内构成（重要，避免误读）

v5 **不是"只有新架构"**，它是 **fork 底座 + 新架构层**：

| 层 | 文件 | 说明 |
|----|------|------|
| 新架构层 | `orchestrator.py` | supervisor 图 + 7 张 per-role 图（**v5 唯一的"新"文件**） |
| 底座 | roles / series / guards / validate / decision / config / llm / media/ / skills/ / tests/ | 源自 `new_deep_agent` fork（`new_deep_agent`→`v5` 改名），是**唯一可靠的生产形态** |

**底座为何必须保留（两个硬依赖）**：
1. 新架构的角色图**复用底座的角色装配**：`role_chat`（模型工厂）、`role_system_prompt`
   （SKILL 装配）、`role_input`（开工契约）、`FS_TOOLS`（工具白名单）都定义在 **`roles.py`**。
2. v5 要能**自包含地出片**：媒体链入口（`series.py`）必须自带。

**`v5/langgraph.json` 只注册 9 张图**（supervisor + role_* ×7 + media_rerender）。原静态链 DAG 已于
2026-09-12 完全废弃（`graph.py` / `studio_graph.py` 已删除）；`roles.py` 保留装配层。

| 维度 | 现状 |
|------|------|
| 对外引用 | v5 不 import 任何已删包（零处，含函数体内延迟 import） |
| 代码 | 自包含（36 个 .py，不含两处 `__init__`：orchestrator / roles / guards / validate / decision / config / llm / hitl / series / server / vendors / webchain / webmap / webwrite + `media/*` × 23） |
| 角色 SKILL | 自带 `v5/skills/packs/` |
| 图注册 | **`v5/langgraph.json` 是全仓唯一图注册入口**（9 图：supervisor + role_* ×7 + media_rerender）；根 langgraph.json 已删 |
| 质量保证 | 自带 `v5/tests_*.py`（973 例；建议**逐文件**跑，全量 discover 一次跑完很慢） |
| 共享运行时资源 | `projects/` 产物目录、`.env`（密钥只存一份） |

## 基线

fork 自 `new_deep_agent` @ commit `23e8360`（2026-09-11）。**该包已于 `9c60373` 删除**，
故 v5 此后单线演进，不再有"回流 / 移植"问题——历史结构查 git 历史（`<9c60373`）。

## 启动

`v5/langgraph.json` 注册 9 张图，需用 `--config` 显式指定（仓库根没有 langgraph.json）：

```bash
# 从仓库根启动 dev server（端口 2024——与 supervisor 的 AsyncSubAgent 默认 url 一致）
SHORTDRAMA_V5_PROJECT=<项目名> .venv/Scripts/langgraph.exe dev \
    --config v5/langgraph.json --host 127.0.0.1 --port 2024 --no-browser

# 自测（972 例，纯离线）
.venv/Scripts/python.exe -m unittest discover -s v5 -p "tests_*.py" -t .
```

> ⚠️ **端口必须与 `SHORTDRAMA_V5_AGENT_URL` 一致**（默认 `http://127.0.0.1:2024`）：
> supervisor 通过该 url 经 HTTP 调 role 图；端口不一致 = 派发全失败（见下）。

## 架构

- **supervisor 图**：director 主 agent（`create_deep_agent` + `AsyncSubAgent`），
  5 把原生遥控器（start/check/list/update/cancel async task），任务记录走 `async_tasks` channel。
- **per-role 图**：7 张（worldbuilder … reviewer），角色 SKILL / 模型 / FS root 编译期固定；
  input 只收**自然语言任务简报**（避免 LLM 构造结构化参数的不可控性——v1 五轮迭代的教训）。
- **产物**：子 run 间通过共享项目目录交汇；成功后 `record_phase` 记账 → 产出标准兼容的
  `.agent_state.json`（media_gate / `--resume-media` 可直接消费）。
- **媒体链**：图外，`v5/media/pipeline.py`（确定性流水线，非 agent）。

## 部署要点（2026-09-12 实测定位）

> ★ **跨机（本机开发 → 另一台跑生产）** 的约定另见
> （原 `docs/多厂商接入-spec.md` §7，**已随 `docs/` 归档**）：
> 代码零本机绝对路径（已核实）、`.env` 承载机器差异（模板 `.env.example`）、
> 代码用 `git bundle` 走、**`.venv` 绝不可复制（公司机重建）**。

1. **`AsyncSubAgent.url` 必填**（`make_async_subagents()` 已内置默认 `http://127.0.0.1:2024`）：
   url 缺省时走 in-process ASGI transport，**实测失败**——每次派发返回
   `Failed to launch async subagent '<role>': 'NoneType' object is not callable`，
   任务不注册（`async_tasks` 恒空）、零产物、零 trace；而弱模型会**无视该错误
   继续"报告 task_id"**（这才是"派发幻觉"的真正来源）。
   url 指向真实 Agent Protocol server 后恢复正常：**实测 lane-erhu 派发 worldbuilder
   → 真实独立 thread + run（async_tasks 有记录）+ 产物落盘 5312 字/80s**。
   （`lane-erhu` 是老架构项目，产物已于 2026-09-14 清理删除；该结论由 2026-09-12 的
   LangSmith 侧证据独立证实过，不依赖其产物存活。）
   生产部署：`SHORTDRAMA_V5_AGENT_URL` 指向部署地址。
2. 验收仍**以磁盘事实为准**（产物文件 + `async_tasks` channel + SDK thread state）；
   同时**必看 ToolMessage 内容**——工具失败的第一手证据在那里，不要从"产物缺失"
   直接推断"模型幻觉"。

## supervisor 沙箱收敛（2026-09-12 实测落地）

supervisor 默认配置会把三类**不该有的能力**一并挂上；三处均已修：

| 修复 | 机制 | 效果 |
|------|------|------|
| 禁用 GP 同步子代理 | `register_harness_profile("openai", HarnessProfile(general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False)))` | 消除与异步 `start_async_task` 并存的**第二条派发通道**（`task` 工具）——模型可能选错通道 |
| FS 工具白名单 | `middleware=[FilesystemMiddleware(backend=backend, tools=FS_TOOLS)]` | 工具数 **13 → 11**：只留 6 文件工具 + 5 把异步遥控器，消除 `execute` / `delete` |
| backend 路径防护 | `FilesystemBackend(root_dir=..., virtual_mode=True)` | 阻断路径穿越（默认 `False` 无安全保证） |

> 注：模型 `role_chat` 是 `ChatOpenAI` 子类，`_get_ls_params().ls_provider == "openai"`，
> 故 harness profile 按 `"openai"` 注册即可命中。


## 变更记录：静态链废弃（2026-09-12）

- **删除**：`graph.py`（10 节点 DAG 全部废弃）、`studio_graph.py`（静态链 Studio 入口）
- **抽取**：`roles.py` —— 角色装配层（FS_TOOLS / TOOL_SKILL 装配 / role_input /
  `_audio_mode_defect`），supervisor 与媒体链守卫依赖它
- **保留**：`series.py` 的媒体链入口（`--resume-media` / `--monitor` / 审批 CLI）；
  其创作链入口改为**废弃报错**并指引 supervisor
- **唯一创作链**：supervisor（`v5/langgraph.json` 9 图）
- 测试：973 例（`python -m unittest discover -s v5 -p "tests_*.py" -t .`，或逐文件 `python -m unittest v5.tests_<名>`）
