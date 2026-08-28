# 诊断：Skill 沉淀 与 Cron 定时任务

日期：2026-08-28　状态：诊断完成，待排期实施

## 一、Skill 自动沉淀（自我改进）——在工作，但完全静默

### 现状
- **机制完好且默认开启**：`agent._skill_nudge_interval = 10`（`skills.creation_nudge_interval` 可配）。
  每累计 10 次工具迭代后，回合结束时若 `skill_manage` 工具可用且回合正常完成
  （final_response 非空、未中断），后台起一个 review 线程（`turn_finalizer.py:456` →
  `spawn_background_review`），由模型回放对话决定是否 patch SKILL.md / 写记忆。
- **确实在沉淀**：`~/.hermes_portable_data/skills/` 下 33 个 skill，其中
  `classical-music-basics`、`github-codebase-review`、`troubleshooting`、
  `shortdrama-multi-agent`、`langgraph-open-agents` 等均为运行期创建/沉淀；
  hermes.log 也有 `💾 Self-improvement review: Patched SKILL.md in skill
  'github-codebase-review' (1 replacement)` 的成功记录。

### 为什么"感觉没有"
1. **触发有节流**：不是每回合都评，每 10 次工具迭代才评估一次；被中断的回合、空回复不触发。
2. **完全静默**：review 在后台 daemon 线程跑，stdout 被线程级静音，结果只落在
   hermes.log 和 skills 目录——前端没有任何"已沉淀"提示，感知为零。
3. **生效滞后**：沉淀的 skill/memory 在下一个会话的 system prompt 注入后才起作用。

### 改进方案（建议）
- **A1 沉淀可视化**：`background_review_callback` 的结果（`summarize_background_review_actions`
  已有摘要函数）经 tui_gateway 发 `skill.deposited` 事件 → 前端过程流里打印一行
  `💾 已沉淀：skill 'xxx'（1 处修改）/ 记忆已更新`。
- **A2 Skills 管理面板**：侧栏新增"技能"页（IPC 列出 `skills/` 目录 → SKILL.md 解析 →
  查看/编辑/停用），沉淀的成果可见可管。
- **A3 节流可配**：`skills.creation_nudge_interval` 暴露到设置页（默认 10，可调 3~5 更激进）。

## 二、Cron 定时任务——后端有，前端从未实现

### 现状
- **后端有完整 cron 系统**：`cronjob` 工具（agent 可自建定时任务）、cron 存储、
  tests/cron 测试齐全；系统层还有 Hermes_Gateway 计划任务。
- **前端为零**：grep 全 src 无任何 cron 代码。侧边栏的「任务」入口
  （Sidebar.jsx `id:"tasks"`）实际是 **AgentRunMonitor**（运行中 run 的监控），
  不是定时任务；"任务管理器未就绪"指 run 管理器，与 cron 无关。

### 实施方案（建议）
- **B1 后端路由**：main.js 增加 cron IPC/HTTP 路由，桥接 hermes cron 的
  list/create/pause/resume/delete（对齐 9120 dashboard 已有的能力）。
- **B2 前端面板**：侧栏新增「定时任务」页——任务列表（name/schedule/next-run/
  启停开关）、新建向导（自然语言描述 → cron 表达式，可直接调 cronjob 工具的
  解析逻辑）、删除/编辑。
- **B3 运行通知**：cron 触发的 run 完成后经微信/gateway 通知（复用现有微信桥）。

## 排期建议
1. A1（半天，立竿见影——让沉淀"被看见"）
2. B1+B2（cron 面板，1~2 天）
3. A2/A3（技能管理页与节流配置）
