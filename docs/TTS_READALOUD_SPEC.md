# Abcyesno — TTS 朗读功能技术规范 (Spec)

> 状态：待评审（确认后进入实现）
> 关联：`MessageActions.jsx`（现有手动朗读）、`electron/backend/agui-server.js`（`/api/transcribe`）、`electron/preload.js`（`transcribeAudio`）
> 决策：音源/音色走 **edge-tts 云端 TTS**（非浏览器 `speechSynthesis`）

---

## 1. 背景与目标

### 1.1 现状
`src/components/MessageActions.jsx` 已实现单条 assistant 消息的手动朗读按钮（`handleSpeak`，基于 `window.speechSynthesis`）。但它存在三个硬限制：

- 音色与速度**硬编码**（`u.lang = "zh-CN"`、`u.rate = 1.0`），无法配置；
- 没有**自动朗读**（agent 回复完成即播）；
- 没有**全局静音 / 播放**控制，且中文音色强依赖用户操作系统已安装的中文语音包（Windows 默认常常缺失）。

### 1.2 目标
1. agent 的信息回复**自动朗读**（可开关）；
2. 提供**静音 / 播放**控制（全局）；
3. 设置界面可**选择音色**；
4. 设置界面可**调节速度**。

### 1.3 范围决策
音源与音色统一走 **edge-tts 云端 TTS**：音色来自微软云端神经语音（如 `zh-CN-XiaoxiaoNeural`），不依赖用户本机语音包，跨平台一致、中文质量好、免费且无需 API Key。

---

## 2. 设计决策与理由

| 决策点 | 选择 | 理由 |
|---|---|---|
| 音源 | edge-tts 云端 | 不依赖用户系统语音包；中文神经语音自然；跨平台一致；免费无需 Key |
| 托管位置 | `agui-server.js`（Node 主进程） | edge-tts 走 WebSocket 协议，浏览器/renderer 直接跑不稳；Node 主进程已有 `fetch` 外部 API 的模式（`/api/transcribe`）可镜像 |
| 前端播放 | `<audio>` 元素 | 拿到 mp3 后标准播放，可控、可暂停/停止，不受系统 TTS 限制 |
| 接入模式 | 复用 transcribe 链路 | 已有 `preload.transcribeAudio` → `fetch /api/transcribe` 对称封装，TTS 完全镜像，零新基础设施 |

**为什么不继续用 `speechSynthesis`**：它是浏览器接口，只枚举系统已装 voice，中文音色缺失时列表为空且无法补救；且 Chromium 在 Electron 下 `getVoices()` 首次常返回空、需 `voiceschanged` 异步回填。云端方案把这些不确定性收口到后端固定音色清单。

---

## 3. 总体架构

```
React 前端 (SettingsPanel / MessageActions / ChatLayout)
        │  window.hermes.synthesizeSpeech(text, voice, rate)
        ▼
preload.js  (contextBridge, 镜像 transcribeAudio)
        │  fetch http://localhost:AGUI_PORT/api/tts
        ▼
agui-server.js  (Express, Electron Main)
        │  edge-tts (Node) ──► 生成 mp3 音频流
        ▼
Microsoft TTS 边缘端点（云端，需联网）
        │
        ▼  返回 mp3 (base64 JSON，与 transcribe 一致)
React 前端 <audio> 播放
```

数据流与现有 STT 完全对称：前端要音频 → 主进程生成 → 返回 → 前端播。

---

## 4. 后端设计（`agui-server.js` + `preload.js`）

### 4.1 依赖
- `npm install edge-tts`（装到 `agui-server` 可解析的 `node_modules`，**必须随 release 分发打包**）。
- 若 Node 包不稳定，备选：在 `hermes-fork` 的 Python venv 装 `edge-tts`，`/api/tts` 转发到 9120 网关执行。当前推荐先用 Node 包，保持自包含、不动 Python 分发。

### 4.2 路由：新增 `app.post('/api/tts')`（镜像 `/api/transcribe`，约 1646 行）
- 入参（JSON）：`{ text: string, voice?: string, rate?: number }`
  - `voice` 默认 `zh-CN-XiaoxiaoNeural`；
  - `rate` 默认 `1.0`（UI 倍速）。
- 处理：
  1. 校验 `text` 非空（超长建议前端分片，见 §6.3）；
  2. 调用 edge-tts 生成 mp3 buffer；
  3. `rate` 映射为 edge-tts 接受的形式（倍数如 `"1.0"` / `"1.5"`，或 `"+50%"`，**以实测为准**）；
  4. 返回 `{ audio: <base64>, mime: "audio/mpeg" }`（复用 transcribe 的 base64 JSON 模式，无需改 preload 二进制处理）。
- 错误：网络失败 / 超时 / 生成异常 → 返回 `{ error: "..." }`，**不抛 5xx**，前端 toast 降级。

### 4.3 preload 封装（镜像 `transcribeAudio`，约 75 行）
- 新增 `synthesizeSpeech: async (text, voice, rate) => { ... }`：
  - `port = await ipcRenderer.invoke('get-agui-port')`；
  - `fetch('http://localhost:${port}/api/tts', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({text, voice, rate}) })`；
  - 返回 `{ audio, mime }` 或带 `error`。

### 4.4 预置音色清单（前端静态，无需动态枚举）
微软中文神经语音常用项（SettingsPanel 下拉直接用，不依赖系统）：

| 显示名 | voice |
|---|---|
| 晓晓（女·默认） | `zh-CN-XiaoxiaoNeural` |
| 云希（男） | `zh-CN-YunxiNeural` |
| 晓伊（女·俏皮） | `zh-CN-XiaoyiNeural` |
| 云扬（男·新闻） | `zh-CN-YunyangNeural` |
| 晓辰（女） | `zh-CN-XiaochenNeural` |
| 云健（男） | `zh-CN-YunjianNeural` |

---

## 5. 前端设计（`src/`）

### 5.1 设置持久化 — `App.jsx`
- 新增 state（仿 `theme` 处理，约 940 行）：
  ```js
  const [ttsSettings, setTtsSettings] = useState(() => {
    try { return { autoRead:false, voice:'zh-CN-XiaoxiaoNeural', rate:1.0,
                   ...JSON.parse(localStorage.getItem('abcyesno:tts')||'{}') }; }
    catch { return { autoRead:false, voice:'zh-CN-XiaoxiaoNeural', rate:1.0 }; }
  });
  function handleTtsSettingsChange(next){
    setTtsSettings(next);
    try { localStorage.setItem('abcyesno:tts', JSON.stringify(next)); } catch {}
  }
  ```
- 将 `ttsSettings` / `onTtsSettingsChange` 传给 `SettingsPanel`，并将 `ttsSettings` 传入 `TtsProvider`。

### 5.2 设置分组 — `SettingsPanel.jsx`
新增「语音朗读」分组（置于「外观」之后），新增 props：`ttsSettings`、`onTtsSettingsChange`：
- **自动朗读**：开关（toggle，复用现有 seg/开关样式）；
- **音色**：下拉 `<select>`，选项来自 §4.4 静态清单；
- **速度**：滑块 `range` 0.5–2.0，步进 0.1，显示「x.x 倍速」。

### 5.3 全局 TTS 控制器 — 新建 `src/hooks/useTts.js` + `TtsContext`
- Provider 包裹 App 根（在 `main.jsx` 或 `App.jsx` 外层），接收 `ttsSettings`。
- 持有状态：`mute`、`isPlaying`、`currentMsgId`、`audioUrl`、`audioEl`（单一 `<audio>` 引用）。
- 暴露方法：
  - `speak(text, msgId)`：若 `mute` 或文本为空返回；若已有播放先 `stop()`；调 `window.hermes.synthesizeSpeech(text, voice, rate)` → 拿到 base64 → 设为 `audioEl.src` → `play()`；维护 `isPlaying=true`、`currentMsgId=msgId`；`onended` 复位。
  - `stop()`：`audioEl.pause()` + 复位 `currentMsgId`（保留 `audioUrl` 以便续播可选）。
  - `setMuted(bool)`。
- **单例播放**：全局同一 `<audio>`，切换消息先停旧的，避免串音。
- **长文本**：优先验证 edge-tts 单次上限；若需分片，`speak` 内部按标点切分、逐段请求并顺序播放（队列）。

### 5.4 消息级朗读按钮 — `MessageActions.jsx`
- 删除 `handleSpeak` 中的 `window.speechSynthesis` 逻辑，改为调用 `useTts()` 的 `speak(text, message.id)` / `stop()`；
- 按钮高亮状态改为：`isPlaying && currentMsgId === message.id`（保证全局只有一个高亮，与 Context 同步）；
- 卸载 cleanup：仅当「当前正在播放且 `currentMsgId === message.id`」时 `stop()`，**不**在其它消息卸载时误中断（解决 Virtuoso 虚拟列表卸载误 cancel，见 §6.4）；
- 图标复用：`audio`（播放）/ `stop-circle`（停止）已内置。

### 5.5 自动朗读 — `ChatLayout.jsx`
- 新增 effect，依赖 `[messages, loading, streamPhase, ttsSettings.autoRead, mute]`：
  - 取最后一条消息；若为 assistant 且由「流式生成中」（`isStreamingText`）变为完成（`loading=false` 且 `streamPhase!=='text_generating'`），且 `ttsSettings.autoRead && !mute` → 触发 `speak(lastText, lastId)`；
  - **防重读**：`useRef` 记录已自动朗读过的 message id；仅在「本次新生成完成」触发，跳过历史消息与组件重挂载（见 §6.5）；
  - 用户手动点停止后，用 ref 标记「已手动干预」，避免 effect 立即重读。

### 5.6 全局静音 / 播放按钮 — `ChatLayout` header
- 在右上角图标排（`settings` / `activity` 等，约 205–214 行）新增两个 `header-icon` 按钮，状态读 `TtsContext`：
  - **静音切换**：`audio` ↔ `volume-x`（后者需补一个 Icon，或直接用状态文案）；点击 `setMuted(!mute)`，作为自动朗读总闸；
  - **播放 / 停止**：朗读中显示 `stop-circle`（点击 `stop()`），空闲显示 `audio`（点击重读最后一条 `speak(lastText, lastId)`）。

---

## 6. 关键风险与坑

1. **联网依赖**：edge-tts 需访问微软端点；离线 / 防火墙环境必失败 → 降级 toast，**绝不阻断聊天与输入**。
2. **分发打包**：`edge-tts` 必须随 release 打包进 `node_modules`；便携构建后需实测 `require('edge-tts')` 可用（参考现有 `express`/`cors` 分发方式）。
3. **长文本**：验证 edge-tts 单次字符上限；超长回复前端分片顺序播放，避免静默截断。
4. **Virtuoso 卸载**：`MessageActions` 在气泡滚出视口会被卸载，cleanup 必须只停「当前条」，由 Context 持有 audio 引用，避免误中断其它条。
5. **流式完成时机**：自动朗读必须等消息**真正完成**（`loading=false`）再读，否则读到半成品；用 `isStreamingText` 判定。
6. **并发串音**：同一时刻只播一个 `<audio>`；切换消息 / 手动停止先停旧的。
7. **速率映射**：edge-tts `rate` 用倍数或 `+/-%` 字符串，与 UI 倍速滑块映射需实测校准。
8. **自动 vs 手动冲突**：手动停止后自动朗读 effect 不应立刻重读——用 ref 标记手动干预状态。

---

## 7. 实施步骤（分阶段，确认后执行）

- **P0 后端**：`npm i edge-tts` → `agui-server.js` 加 `/api/tts` → `preload.js` 加 `synthesizeSpeech`。
- **P1 前端基础设施**：`App.jsx` 持久化 `ttsSettings` + 新建 `TtsProvider` Context。
- **P2 设置面板**：`SettingsPanel` 加「语音朗读」分组（自动朗读 / 音色 / 速度）。
- **P3 消息按钮 + 全局按钮**：`MessageActions` 改用 Context；`ChatLayout` header 加静音/播放。
- **P4 自动朗读**：`ChatLayout` 自动朗读 effect（含防重读 / 手动干预）。
- **P5 质量门**：TDZ 干净 → `vite build` → `scripts/test-multisession` 全绿 → `dist` 镜像到 `release/win-unpacked` → 完整退出并重启 `Abcyesno.exe` → `git commit & push`。

---

## 8. 测试 Prompt 与验收标准

1. **手动朗读**：设置选不同音色 / 速度，点消息朗读按钮，听音色与速度变化生效。
2. **自动朗读**：开启「自动朗读」，发送一条消息，agent 回复**完成后**自动朗读。
3. **全局控制**：朗读中点 header 停止 / 静音即时生效；切换到另一条消息不串音。
4. **降级**：断网时朗读失败仅 toast 提示，聊天与输入不受影响。
5. **持久化**：关闭重开设置，音色 / 速度 / 自动朗读开关保持。

---

## 9. 范围外（Out of Scope）

- 不接入系统本地 `speechSynthesis` 作为离线 fallback（如需可后续加，作为云端失败时的备选通道）。
- 不打包离线 TTS 引擎（如 eSpeak / 本地神经模型）。
- 不实现逐字高亮（word-level highlighting）等增强，本期仅整段朗读。
