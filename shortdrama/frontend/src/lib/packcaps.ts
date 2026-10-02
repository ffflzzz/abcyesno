/* ==========================================================================
   src/lib/packcaps.ts —— 类型包能力的中文展示（**判据只留一份**）
   --------------------------------------------------------------------------
   后端 `GET /styles` 一直给 `audio_modes` / `still_refs` / `has_style_block`
   （见 `v5/webmap.py` 的 `styles()`：直接从每个包的 `pack.json` 读，不硬编）。
   新建项目页与向导页都要展示它 ⇒ 中文标签与"没声明时说什么"必须同一套，
   否则两个页面对同一个包会给出两种说法（本项目最忌"同一件事两个口径"）。

   ⚠️ 这里**只翻译标签**，不重新判定任何东西：值一律取后端给的，
   表里没列到的模式名**原样显示**（不猜）。
   ========================================================================== */

import type { StylePack } from '../types';

/** `pack.json` 的 `audio-modes` → 给人看的中文。没列在表里的值原样显示。 */
export const AUDIO_MODE_ZH: Record<string, string> = {
  silent: '无台词',
  'dialogue-led': '有台词',
  'narration-led': '旁白',
};

export interface PackCaps {
  /** 配音模式一行；包里没声明时说的是**回落行为**，不是"没声音" */
  audio: string;
  /** 静帧是否绑参考图（绑了才不会同一张脸在每镜自己编） */
  refs: string;
  /** 没有 style-block.md 时的提醒（逐镜提示词里没有风格锁定）；有则空串 */
  block: string;
}

export function packCaps(s: StylePack): PackCaps {
  const modes = (s.audio_modes || []).map((m) => AUDIO_MODE_ZH[m] || m).filter(Boolean);
  return {
    audio: modes.length
      ? '配音：' + modes.join(' / ')
      // 这条是真会踩到的：`brief` 不写 `audio_mode` 时后端一律回落 `dialogue-led`
      // （`validate.audio_mode_of`），而 `pack.json` 的 `default-audio-mode` **代码不消费**。
      : '配音：包里没声明 audio-modes（brief 不写 audio_mode 时一律按「有台词」跑）',
    refs: s.still_refs === true
      ? '静帧绑参考图'
      : (s.still_refs === false
        ? '静帧不绑参考图'
        : '静帧参考图策略未声明（缺 pack.json 时走默认包回落）'),
    block: s.has_style_block === false
      ? '该包没有 style-block.md：逐镜提示词里没有风格锁定'
      : '',
  };
}
