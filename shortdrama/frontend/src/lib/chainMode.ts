/* ==========================================================================
   src/lib/chainMode.ts —— 创作链的「逐步确认」开关
   --------------------------------------------------------------------------
   管的是**步级 HITL**：开着时 supervisor 每派发一个角色之前 `interrupt`，
   等人在底部确认条上点「继续 / 打回重做」才走下一步（信道见 `v5/hitl.py`）。

   为什么默认**关**（一口气跑完 7 个角色）：
     逐步确认 = 一次完整创作链要点 7 次按钮，而绝大多数时候人只是想拿到分镜表。
     真要盯片的人会把开关打开 —— 那时底部确认条（`components/HitlBar.tsx`）才出场。
     与 `lib/quality.ts` 同一条纪律：开关跟着**路径**走，CLI / 外部 agent 不受影响。

   ⚠️ 这个值是**编译期**烘进 dev server 的（`orchestrator._interrupt_on()` 在
   模块级 `build_supervisor()` 里被调），所以它不是"随请求走"的普通参数：
   后端 `webchain.ensure_devserver` 发现运行中的 dev 与本值不一致时会**重启 dev**
   （约 6 秒）。⇒ 拨完开关的**下一次**生成才生效，正在跑的链不受影响。
   ========================================================================== */

const KEY = 'sd.chainMode.v1';

const state = { manualSteps: false };

function load(): void {
  try {
    const s = JSON.parse(localStorage.getItem(KEY) || '{}') as { manualSteps?: boolean };
    state.manualSteps = !!s.manualSteps;
  } catch { /* 坏数据当默认值用，别让页面打不开 */ }
}
load();

function save(): void {
  try { localStorage.setItem(KEY, JSON.stringify(state)); } catch { /* 隐私模式等 */ }
}

/** 开关当前值。 */
export function manualStepsOn(): boolean { return state.manualSteps; }

/** 翻转，返回新值。 */
export function manualStepsToggle(): boolean {
  state.manualSteps = !state.manualSteps;
  save();
  return state.manualSteps;
}

/**
 * 放进**创作链类请求体**的字段（`1`/`0` 而非布尔，与 `qcPayload` 同一理由：
 * 数字最不容易两边理解不一致）。后端拿它决定起 dev server 时带不带
 * `SHORTDRAMA_APPROVE_EACH_ROLE`。
 */
export function chainModePayload(): { manual_steps: number } {
  return { manual_steps: state.manualSteps ? 1 : 0 };
}

export const MANUAL_STEPS_LABEL = {
  label: '逐步确认',
  hint: '打开：每个角色开工前停下来等你点「继续 / 打回重做」（7 个角色要点 7 次）。'
    + '拨动后需下一次生成才生效（会重启 dev server）。',
};

export const ChainMode = { on: manualStepsOn, toggle: manualStepsToggle, payload: chainModePayload, state };
