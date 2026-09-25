# 死代码审计报告

> 审计日期：2026-09-25　审计范围：自研代码（`src/`、`electron/`、`scripts/`、`shortdrama/v5`、`shortdrama/web`、`shortdrama/frontend`）
> 未覆盖：`hermes-fork/`（fork 的上游项目，其"死代码"属于上游资产，动它会与后续合并冲突）、`node_modules/`、`build/`、`release/`、`shortdrama/projects/`（媒体产物）

## 一句话结论

**真正能放心删的只有 12 个前端孤立文件和 13 个 Python 零引用函数/常量。** 其余 200+ 个"看起来没人用"的符号里，绝大多数是活代码——它们被路由表、字符串反射、动态索引或构建入口调用，静态分析看不见。另有 3 个发现不是死代码，而是**功能已经悄悄失效**（开关没人读），性质比死代码严重。

## 二、审计方法

三类证据交叉验证，不做单一工具的结论：

| 证据来源 | 覆盖 | 说明 |
|---|---|---|
| 自研 AST 扫描器（`tmp/deadcode/scan-js.cjs` + `analyze-js.cjs`） | JS/TS 220 文件 | 建 import 图（含动态 `import()`、`require()`、HTML `<script src>`、TS 的 `./x.js`→`x.ts` 后缀映射），从 55 个入口做可达性遍历；再统计每个导出符号的跨文件引用 |
| vulture 2.16 + 自研取证脚本（`tmp/deadcode/verify-py.py`） | Python | vulture 出候选，再用脚本把每个候选名字拿去全仓库（含 `.md`/`.json`/`.env`）搜，区分"只有定义行"与"别处有引用" |
| 项目自带 `shortdrama/scripts/audit_dead_files.py` | shortdrama 包契约 | 按**加载机制**判定（约定加载 vs 引用加载），与上面的文本引用法互补 |

外加人工复核：每个候选都回源码确认语义、注释意图、以及与 `docs/*.md` 中 spec 的对应关系。

## 三、结论分级

### A 级 —— 确认可删（零引用、无动态调用、无契约关系）

**A1. 前端孤立组件 8 个（含 1 个 hook）**

| 文件 | 证据 |
|---|---|
| `src/components/ToolCard.jsx` | 全项目零 import；仅被注释提及。已被 `TerminalToolCard.jsx` 取代（后者注释自证"视觉外壳与 ToolCard 一致"） |
| `src/components/TerminalPanel.jsx` | 唯一引用者是 `ToolCard.jsx`（已死），成链条 |
| `src/components/ApprovalDialog.jsx` | 零 import；`ApprovalBubble.jsx` 注释自证 "Replaces the modal ApprovalDialog" |
| `src/components/AgentVerboseTimeline.jsx` | 零 import；对应 `docs/AGENT_VERBOSE_SPEC.md` 的设计未接线 |
| `src/components/StructuredThinking.jsx` | 零 import；对应 `docs/AGENT_LOOP_ANIMATION_SPEC.md` |
| `src/components/TaskProgressPanel.jsx` | 零 import；`MessageThread.jsx:1233` 注释提到它，但代码里没有渲染它 |
| `src/components/DevConsole.jsx` | 零 import，无任何文档提及 |
| `src/hooks/useVirtualRows.js` | 零 import；`MessageThread.jsx` 实际用 `react-virtuoso` 实现虚拟滚动 |

> 共同特征：都是"按 spec 写过、后续被替换"的设计遗骸。删掉后 `npx vite build` 不受影响（无入边）。

**A2. Python 零引用函数/常量 12 个**

| 位置 | 名称 | 证据 / 备注 |
|---|---|---|
| `v5/config.py:48` | `_split_keys` | 已被 `_split_keys_with_base`（同文件 :64，注释自称 2026-09-22 起的"唯一解析点"）取代。**它的存在违反项目"不要两处解析"的规范** |
| `v5/config.py:144` | `base_for_key` | 全仓库零出现 |
| `v5/config.py:209` | `ORCH_PROVIDER` | 空串常量，零引用 |
| `v5/config.py:212` | `VIDEO_PROVIDER` | 零引用 ⇒ `SHORTDRAMA_VIDEO_PROVIDER` 环境变量已失效（见第五节） |
| `v5/media/prompt.py:1339` | `build_all` | 零调用；实际调用的是 `build_still_prompt` / `build_video_prompt` |
| `v5/media/sheet.py:53` | `PIECES` | 零引用 |
| `v5/media/storyboard.py:56` | `OPTIONAL_KEYS` | 零引用（注释称 2026-09-08 为"可选列"新增，实际解析走别的路径） |
| `v5/media/video_plan.py:180` | `wants_prev_tail` | 方法零调用（mixed 模式的逐镜决策已由别处实现） |
| `v5/webmap.py:245` | `asset_names` | 零引用 |
| `v5/validate.py:224` | `DIALOGUE_LONG_WARN_RATIO` | 常量 = 0.0，零引用（"超长台词只告警"这条规则实际没读它） |
| `v5/vendors.py:336` | `impl_dir` | 零引用（docstring 说是"给提示用"，但没有任何提示调用它） |
| `scripts/draw_app_paper_icon.py:95` | `band_c` | 局部变量，赋值后从未使用 |

> 行号以审计当日（删除前）的文件为准。原先误记为 `v5/media/webmap.py`、`v5/media/validate.py`，实际这两个模块在 `v5/` 根下。

**A2 例外（复核改判：不删）** —— vulture 另报了两处不可达代码，复核后判定**保留**：

| 位置 | 原始判定 | 改判理由 |
|---|---|---|
| `shortdrama/scripts/drive_chain.py:164` | `while` 之后不可达 | 那是一个 `return None` **兜底返回**。虽然 `while True` 结构下确实走不到，但它属于"防御性分支"（第 4 类）——一旦循环条件将来改动，缺了它函数会掉出返回值 |
| `shortdrama/v5/series.py:269` | `raise` 之后不可达 | `raise SystemExit("[deprecated] v5 静态链创作链已废弃…")` 之后跟着的是**整段已废弃路径**（媒体链旧调用方式）。删它等于抹掉一整个废弃分支的历史逻辑，属于设计决策而非垃圾清理，需单独确认 |

**A3. 零引用导出（TS/JS）**

| 位置 | 名称 |
|---|---|
| `electron/backend/wechat_bridge/src/pending-queue.ts` | `appendPending`、`clearPending`、`hasPending`（同文件的 `loadPendingQueue`/`savePendingQueue` 是活的，这 3 个是另一套未被采用的 API） |
| `wechat/cdn.ts` | `buildCdnDownloadUrl` |
| `wechat/crypto.ts` | `generateAesKey` |
| `wechat/upload.ts` | `isImageFile` |
| `wechat/types.ts` | `GetUpdatesReq` |
| `bridge.ts` | `makeWechatThreadId`、`BridgeStatus` |
| `electron/backend/edgeTtsClient.js` | `getGecToken` |
| `shortdrama/frontend/src/lib/chainMode.ts` | `manualStepsOn`、`manualStepsToggle` |
| `shortdrama/frontend/src/lib/quality.ts` | `qcOn` |
| `shortdrama/frontend/src/router.ts` | `__setHashForTest`（命名即"测试用"，但测试里没用） |
| `shortdrama/frontend/src/store.ts` | `probeBackend` |
| `src/components/Onboarding.jsx` | `getOnboarding`（同文件 `isOnboardingDone` 是活的） |
| `src/contract/eventBus.js` | `clearContractEvents` |

### B 级 —— 疑似死，但建议先不动（预留接口 / 未接线功能）

| 位置 | 为什么不建议直接删 |
|---|---|
| `v5/series.py:467` `_require_approval` | **不是垃圾，是没接线的功能**。它读 `config.REQUIRE_APPROVAL`，而全项目只有它读这个开关 ⇒ `SHORTDRAMA_REQUIRE_APPROVAL=1` **当前完全无效**（审批门形同虚设）。删掉＝永久放弃这道门；正确做法是接线或明确宣布废弃（见第五节） |
| `v5/media/scaffold.py:19` `ANTI_TEXT` | 注释明写"保留常量名（旧代码/测试可能引用）"——作者有意保留的兼容常量 |
| `v5/media/qc.py:169` `current_text_policy` | docstring 写"诊断/日志用"，是有意留的公开 helper |
| `v5/media/model_profile.py:252` `text_concept_re` | docstring 写"留给需要按正则扫描的调用方"，同上 |
| `v5/media/assets.py:144` `_safe_ref_url` | docstring 写"兼容旧调用"，是兼容壳；调用方已迁走 |
| `shortdrama/web/assets/js/core/api.js` 的 11 个方法 | `_local`/`createByPaste`/`updateOutline`/`getAssetRefs`/`estimateCredits`/`listRuns`/`getEpisodeList`/`getStoryboard`/`exportEpisode`/`getStyles`/`getVendors` 全部零调用。但它们是**后端接口的映射层**，记着接口形状；删了等于丢掉接口知识 |
| `shortdrama/web/assets/js/ui/overlay.js:64` `setCount` | 是 `api` 对象的成员，通过 `o.onReady(api)` 交给调用方——属于对外回调契约的一部分 |
| `shortdrama/web/assets/js/views/wizard.js:764` `generateAssetState` | 资产生成本地模拟逻辑，零调用（该交互未接线） |
| `wechat_bridge/src/tools/visualize-logs.ts` | 见下方 C 级说明——**实为入口，不该删** |

### C 级 —— 看着死、其实活（**禁止删**，这部分是本次审计的主要价值）

| 现象 | 实例 | 为什么工具会误判 |
|---|---|---|
| **HTTP 路由 handler** | `v5/server.py` 的 `vendors_route`、`projects`、`gen_keyframe`、`gen_video`、`gen_script`、`runs_list`、`hitl_get` 等 **20+ 个** | 注册在 `@r.get(...)` / `@r.post(...)` 上，还有一条 `@app.api_route("/{rel:path}")` 通配兜底路由——路由表是框架在启动时装配的，静态看不见 |
| **LangGraph 图入口** | `v5/orchestrator.py:481` `supervisor_graph` | `v5/langgraph.json` 用字符串 `"v5.orchestrator:supervisor_graph"` 引用它 |
| **字符串反射读取配置** | `v5/config.py` 的 `CHAT_BASE_EFFECTIVE`、`CHAT_KEY_EFFECTIVE` | `v5/vendors.py:133` 用字符串表 `"base_from_config": "CHAT_BASE_EFFECTIVE"` + `getattr` 取值——**典型"看着死、其实活"** |
| **跨模块 monkeypatch** | `scripts/_kf_demo.py:52`、`scripts/probe_keyframes_real.py:9`、`scripts/_test_bridge.py:60` 的 `am.MOCK = False` | 这是给**外部模块** `mc_services.agnes_media` 设开关的副作用，本地没有读取动作，但它就是目的 |
| **动态键索引的对象字典** | `shortdrama/web/assets/js/ui/icons.js` 的 `I` 图标字典（`eye`、`down` 等被报零引用） | `shell.js:14` 用 `I[n.icon](28)` 按名字动态取——整个字典都是活的 |
| **shebang CLI 工具** | `electron/backend/wechat_bridge/src/tools/visualize-logs.ts` | 文件头有 `#!/usr/bin/env node` 和 usage 注释，靠 `npx tsx` 手动运行 |
| **构建入口的传递依赖** | `wechat_bridge/src/**`（main.ts、session.ts、wechat/*.ts 等 20 个） | `build.mjs` 的 `entryPoints` 是 `src/bridge.ts`，这些文件经 import 树被 esbuild 打进 `dist/index.js` |
| **框架注入的函数参数** | `v5/server.py:388` `include_content` | FastAPI 从 query string 注入（前端确实传 `?include_content=true`），不是未使用变量 |
| **测试用例** | `v5/tests_*.py` 全部 | pytest 收集，靠命名约定而非 import |
| **工具配置入口** | `shortdrama/frontend/vite.config.ts`、`vitest.config.ts` | 被 vite / vitest CLI 读取 |
| **约定加载的资源** | `v5/skills/packs/*/SKILL.md`、`pack.json`、`style-block.md` | 路径由 `roles._role_skill` 等按角色名拼接，文本零引用是正常的（项目自带审计脚本已按此判据处理） |
| **多余但无害的 export** | 如 `src/App.jsx` 的 `runStudioWorkflow`、`src/hooks/useTts.jsx` 的 `DEFAULT_TTS_SETTINGS` | 名字在**自己文件内**仍被使用，只是 `export` 关键字多余 ⇒ **不是死代码**，删 export 关键字即可（可选清理，共 33 处） |

### D 级 —— 文件级垃圾（非源码逻辑问题）

| 对象 | 体积 | 说明 |
|---|---|---|
| `shortdrama/tmp/` | **395 MB / 23933 文件** | 实验工作目录（cutprobe、ref8、mingan 分析脚本等）。项目自带审计脚本也把它们列为"疑似死模块" |
| `releasewin-unpackedresourcesappdist/` | 8.8 MB | **路径分隔符被吞掉形成的畸形目录**（本应是 `release/win-unpacked/resources/app/dist/`），内含重复的 dist 副本 |
| `dist-preview/index.html` | 6.0 MB | `scripts/make_self_contained_preview.cjs` 的产物，可随时重生成 |
| `nul` | 328 B | Windows 保留设备名，误创建的无效文件 |
| `studio-ui-prototype.html` | 30 KB | UI 原型稿，已无引用 |
| 根目录 `test_interrupt_probe.py`、`test_manju_hitl.py`、`test_values_mode.py` | 共 217 行 | 一次性探针脚本，全项目零引用 |
| 根目录 `*.log`（`abcyesno-test.log`、`abcyesno-portable-test.log`、`npm-install.log`、`test_hitl.log`） | — | 运行日志 |
| `scripts/_kf_demo.py`、`scripts/_test_bridge.py` | — | 下划线前缀＝临时脚本（**但注意**：它们含 `am.MOCK` 跨模块设置，确认不再需要再删） |

## 四、附带修好的问题

- `shortdrama/scripts/audit_dead_files.py` 会**崩溃**：某个顶层为数组的 `.json` 触发 `AttributeError: 'list' object has no attribute 'get'`。已加 `isinstance(d, dict)` 守卫，现已可正常运行（本次审计用它跑出了 19 个"疑似死模块"）。

## 五、发现的真问题（比死代码严重）

这三个是**功能静默失效**，文档和环境变量都在骗人：

1. **`SHORTDRAMA_STILL_FIRST` 已失效** — `shortdrama/README.md:288` 仍把它列为可配项（"静帧先行：预演静帧即生产首帧，0 = 退回旧模式"），但代码里 `STILL_FIRST` 全项目零读取。对比：`STILL_CHAIN`、`STILL_RATIO`、`VIDEO_BGM` 都有人读，说明这条是在重构中被漏掉的。
2. **审批门形同虚设** — `SHORTDRAMA_REQUIRE_APPROVAL=1` 不生效，因为唯一读它的 `_require_approval()` 没有任何调用方。
3. **`SHORTDRAMA_VIDEO_PROVIDER` 失效** — 同上，`VIDEO_PROVIDER` 零引用。

这三条要么接线，要么从文档里删掉描述——现在的状态是"以为有防护，其实没有"。

## 六、待你决策

1. **两套前端并存**：`shortdrama/web/`（原生 JS，19 文件）与 `shortdrama/frontend/`（React+TS，28 文件）是同一产品的两份实现，共用同一后端接口。这不是死代码，但是**最大的结构性重复**——哪一套是权威？另一套是否冻结/归档？这决定了 A3 里 `shortdrama/frontend` 那些零引用导出的处理方式。
2. **D 级垃圾是否清理**：`shortdrama/tmp/`（395 MB）与 `releasewin-unpackedresourcesappdist/`（8.8 MB）我没有动。要清的话建议先移入 `_trash/` 而不是直接删。
3. **B 级"未接线接口"**：`web/assets/js/core/api.js` 的 11 个方法、`generateAssetState`、`setCount` 是接线还是删除？

## 七、复现方式

```bash
# JS/TS（220 文件，55 入口）
node tmp/deadcode/scan-js.cjs && node tmp/deadcode/analyze-js.cjs

# Python 候选取证（候选清单写在脚本 CANDIDATES 里）
python tmp/deadcode/verify-py.py

# shortdrama 包契约（按加载机制判定）
python shortdrama/scripts/audit_dead_files.py

# web 原生 JS（函数定义 vs 调用）
node tmp/deadcode/scan-web-js.cjs
```

> 审计脚本本身放在 `tmp/deadcode/`，属于临时工具，可按需保留或清理。

## 八、执行记录（2026-09-25）

按用户指令"清掉第一类，其他先别动"，已执行以下删除。

### 已删除

**前端整文件 8 个**（`src/` 下）：
`components/ToolCard.jsx`、`components/TerminalPanel.jsx`、`components/ApprovalDialog.jsx`、
`components/AgentVerboseTimeline.jsx`、`components/StructuredThinking.jsx`、
`components/TaskProgressPanel.jsx`、`components/DevConsole.jsx`、`hooks/useVirtualRows.js`

**Python 零引用符号 12 处**：
`v5/config.py` 的 `_split_keys`、`base_for_key`、`ORCH_PROVIDER`、`VIDEO_PROVIDER`；
`v5/media/prompt.py` 的 `build_all`；`v5/media/sheet.py` 的 `PIECES`；
`v5/media/storyboard.py` 的 `OPTIONAL_KEYS`；`v5/media/video_plan.py` 的 `wants_prev_tail`；
`v5/webmap.py` 的 `asset_names`；`v5/validate.py` 的 `DIALOGUE_LONG_WARN_RATIO`；
`v5/vendors.py` 的 `impl_dir`；`scripts/draw_app_paper_icon.py` 的 `band_c`

### 复核后未删（报告原列，实际保留）

- `shortdrama/scripts/drive_chain.py:164` 的兜底 `return None` —— 防御性分支，见 A2 例外表
- `shortdrama/v5/series.py:269` 之后的废弃路径 —— 属设计决策，非垃圾

### 本次未动（按要求）

A3 的 13 个零引用导出、B 级"预留接口/未接线功能"、D 级文件级垃圾，以及第五节那三个失效开关。

### 验证结果

| 检查 | 结果 |
|---|---|
| `scripts/check-tdz.js` | clean（0 violations） |
| `scripts/check-undef.js` | clean（0 suspicious refs / 126 files） |
| `vite build` | 通过，2127 modules，9.98s |
| `py_compile`（9 个改动文件） | 通过 |
| `shortdrama/scripts/check_docs.py` | 文档对账全部通过（9 图 / 23 media 模块 / 5 技法 / env 对账一致） |
| `pytest tests_validate + tests_webmap + tests_vendors` | **175 passed / 4 failed** |

> ⚠️ 那 4 个失败**不是本次删除引起的**：已用改动前的 `config.py` 复跑同一组用例，失败完全相同。
> 全部落在 `tests_vendors.py::TestChatLane`，根因是 2026-09-23"文本通道接入国内入口"的改动
> （`CHAT_BASE_EFFECTIVE` 优先于 `AGNES_BASE`）之后，测试仍 mock `AGNES_BASE` 并断言 `base_url`
> 等于它 —— 属**既有测试红灯**，需另行修复测试断言。

### 备份

删除/修改前的所有文件备份在 `tmp/deadcode/_backup_20260925/`（19 个文件，356 KB），
可逐个还原。改动本身**尚未提交**（工作区另有 60 项他人未提交改动，不宜合并提交）。
