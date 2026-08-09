:

# Abcyesno

基于 Hermes 源码二次开发的便携版 Agent 平台。

## 项目目标

- 复用 Hermes 的 harness（agent loop、tools、skills、memory、gateway）。
- 拆除 IM 通道、官方更新、Telemetry 等非必要模块。
- 让 LangGraph Agent（如短剧制片工作台 `manjucraft_agent`）以 Hermes skill 形式接入。
- 用 Electron + 全自研 React 前端替代官方 desktop 前端（基于 AG-UI 协议直连 SSE，不依赖 CopilotKit）。
- 最终打包成即插即用的便携版。

## 文档导航

| 文档 | 说明 |
|------|------|
| [docs/PRD.md](docs/PRD.md) | 产品需求与设计目标 |
| [docs/SPEC.md](docs/SPEC.md) | 技术规格与架构 |
| [docs/UI_UX_SPEC.md](docs/UI_UX_SPEC.md) | 前端 UI/UX 设计规范 |
| [docs/ROADMAP.md](docs/ROADMAP.md) | 开发路线与里程碑 |
| [docs/ACCEPTANCE.md](docs/ACCEPTANCE.md) | 验收标准 |
| [docs/SETUP.md](docs/SETUP.md) | 开发环境搭建 |
| [docs/GLOSSARY.md](docs/GLOSSARY.md) | 术语表 |
| [docs/KNOWN_UNKNOWNS.md](docs/KNOWN_UNKNOWNS.md) | 待确认问题清单 |
| [docs/STRIPPING_GUIDE.md](docs/STRIPPING_GUIDE.md) | Hermes 源码精简指南 |
| [docs/ADR/001-fork-hermes.md](docs/ADR/001-fork-hermes.md) | 架构决策：Fork Hermes |

## 快速开始

### 直接运行便携版（推荐）

> 项目刚改名为 Abcyesno，`release/` 目录下的旧产物已清理，需要重新打包才能生成带新品牌的便携版。

1. 在本目录执行 `npm install && npm run electron:build`。
2. 解压生成的 `release/Abcyesno 1.3.0 win-unpacked.zip` 到任意目录。
3. 进入解压后的 `win-unpacked/` 目录，双击 `Abcyesno.exe` 启动。
4. 首次启动会提示输入 Agnes API Key；输入后应用会自动重启后端。

> 注意：单文件 `Abcyesno 1.3.0.exe` 本次已构建成功，但因体积较大（约 340MB），建议优先使用 `win-unpacked.zip`；在目标机器上实际运行前可先验证 `win-unpacked` 目录。

> 如需旧品牌产物，可查看 `L:\hermes-portable-v6\release\Hermes Portable 1.3.0 win-unpacked.zip`。

### 开发环境

```bash
# 1. 安装 Node.js 依赖
npm install

# 2. 准备 Hermes Fork（详见 docs/SETUP.md）
# 3. 启动开发环境
npm run dev
```

## 当前状态

- Phases 0-4 已完成并通过验证：Hermes Fork 基线、源码精简、Harness 核心能力、LangGraph Skill Adapter、短剧制片工作台（`manjucraft_agent`）接入。
- Phase 5 已完成：Electron 前端已桥接到 Hermes 后端，支持助手选择、消息发送、工具事件卡片、审批弹窗、文件上传、技能面板。
- Phase 6 已完成：`win-unpacked` 便携包可用；单文件 `Abcyesno 1.3.0.exe` 也已构建成功。
- Phase 7 已按 `docs/ACCEPTANCE.md` 逐项验收，详见 `DEV_LOG.md`；真实对话回复仍需有效 Agnes API key 做最终验证。

### 已落地的前端特性

- **图片交互**：输入框与对话气泡中的图片以文件名芯片显示，鼠标悬停才弹出缩略图，不再占据大幅版面。
- **会话列表**：标题自动回退到首条消息、相对时间（今天/昨天/前天/3-7 天前）、未命名会话隐藏消息摘要副标题。
- **会话标题两层方案**：聊天头部显示会话正式标题而非消息截断；新会话首轮结束后由后端 `/api/session-title` 调用 `agnes-2.5-flash` 异步生成 ≤12 字总结标题写入 `session.title`（用户重命名优先）。
- **结果面板脱离**：可将结果区脱离为独立窗口（多窗口并行查看）。
- **短剧制片工作台**：统一视频生产前端（`StudioWorkbench`），覆盖角色图、分镜、成片、剪映草稿导出；提交真实结构化输入走 HITL 审批门。

## 开发路线

详见 [docs/ROADMAP.md](docs/ROADMAP.md)。

## 开发路线

详见 [docs/ROADMAP.md](docs/ROADMAP.md)。
