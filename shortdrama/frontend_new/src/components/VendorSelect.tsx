/* ==========================================================================
   src/components/VendorSelect.tsx —— 模型厂商选择器（分镜页 / 资产页共用）
   --------------------------------------------------------------------------
   为什么抽成共享模块：**分镜页与资产页需要完全同一套逻辑**
   （选项来自后端 / 无厂商可选时禁用并说明 / 选择随生成请求发出）。
   复制两份必然漂移 —— 本项目最贵的一类 bug（「同一判据绝不写两份」）。

   为什么选项必须**来自后端**：厂商由 `v5/media/vendors.py` 的 `register()` 决定，
   清单走 `GET /v1/pixa/short-drama/vendors` 水合进 `Store.state.vendors`。
   前端写死一份列表，公司机 `register("comfy", …)` 之后前端就**看不到它**——
   与类型包同一条教训（v5 加第 4 个包时，硬编列表把它静默拒了）。

   为什么选择**不落 localStorage**：厂商列表是派生数据，落盘的选择在换后端 /
   换机器后会指向一个不存在的厂商 ⇒ 静默偏差。
   ========================================================================== */

import { Store } from '../store';
import type { Vendors } from '../types';

export type VendorKind = 'image' | 'video';

/** 本会话里用户选过的厂商，按能力分开。空 = 还没选 ⇒ 用后端缺省。 */
const chosen: Record<VendorKind, string> = { image: '', video: '' };

const label = (k: VendorKind) => (k === 'video' ? '视频' : '图片');

function info(k: VendorKind) {
  const v = Store.getState().vendors as Vendors | null;
  return {
    items: (v && v.items) || [],
    current: (v && v.current && v.current[k]) || '',
    ok: !!(v && v.current_ok && v.current_ok[k]),
  };
}

export const Vendor = {
  label,
  has: (k: VendorKind) => info(k).items.length > 0,

  /** 当前生效的厂商：用户选的 > 后端缺省 > 第一项；无可选项 ⇒ ''。 */
  pick(k: VendorKind): string {
    const inf = info(k);
    const codes = inf.items.map((x) => x.code);
    if (chosen[k] && codes.includes(chosen[k])) return chosen[k];
    if (inf.current && codes.includes(inf.current)) return inf.current;
    return codes.length ? codes[0] : '';
  },

  /** 请求体里该带的厂商字段。无可选项 ⇒ **不带**，让后端用缺省。 */
  payload(k: VendorKind): Record<string, string> {
    const c = Vendor.pick(k);
    if (!c) return {};
    return { [k + '_vendor']: c };
  },

  onChange(k: VendorKind, value: string): string {
    chosen[k] = value || '';
    // 明确回显，避免"选了但不知道生效没"（本项目纪律：生效/失败必须可见）
    const hit = info(k).items.filter((x) => x.code === chosen[k])[0];
    Store.toast('本次' + label(k) + '生成将使用：' + ((hit && (hit.short || hit.name)) || chosen[k]));
    return chosen[k];
  },

  reset(): void { chosen.image = ''; chosen.video = ''; },
};

export function VendorSelect({ kind, cls = 'vendor-sel', tip }: {
  kind: VendorKind; cls?: string; tip?: string;
}) {
  const inf = info(kind);
  if (!inf.items.length) {
    const why = '厂商列表未取到';
    return (
      <span className={cls + ' is-disabled'} title={why + ' ⇒ 厂商选择不可用'}>
        {why} ▾
      </span>
    );
  }
  const cur = Vendor.pick(kind);
  const title = (tip || ('本次生成使用的' + label(kind) + '厂商'))
    + (inf.ok ? '' : '（⚠️ 后端缺省「' + inf.current + '」未注册，生成会返回 400）');
  return (
    <select
      className={cls}
      data-vendor={kind}
      title={title}
      data-stale={inf.ok ? undefined : '1'}
      value={cur}
      onChange={(e) => Vendor.onChange(kind, e.target.value)}
    >
      {inf.items.map((x) => (
        <option key={x.code} value={x.code}>{x.short || x.name || x.code}</option>
      ))}
    </select>
  );
}
