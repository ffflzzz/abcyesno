/* ==========================================================================
   src/components/AssetDrawer.tsx —— 角色/场景/道具信息面板（全屏抽屉）
   --------------------------------------------------------------------------
   复刻线上点击资产形象后打开的抽屉。结构：
     左栏  名称 / 形象名称 / 声音 / 音色描述 / 应用到所有形象 / 剧集
           / 外观描述（identity）/ [保存] / 后端回报的 ignored 清单
     右栏  单图 + 四视图，各带 [生成][+]
   契约：保存 → `Api.saveAssetState(pid, kind, assetId, body)`

   ★ 两条从旧版搬过来的硬约束：

   ① **支持"新建"模式**（`assetId` 为空）：用户要求「点『＋ 添加』打开的就是这个抽屉」。
      新建时「创建」走 `Api.addAsset`（**真落盘**），不是本地造一条。

   ② **v5 没有语义的控件一律 `disabled` + 写明原因**，并在顶部挂一条横幅说明。
      ⛔ 不能摆一堆"点了没反应"的控件假装功能齐全 —— 那正是本项目最忌的「静默无效」。
      逐项对照（`NO_V5`）：
        · 声音 / 音色描述 —— v5 **没有** per-asset 音色（音频口径在项目级 `brief.audio_mode`）
        · 应用到所有形象 —— v5 每个资产**只有一份形象**
        · 剧集         —— v5 的资产是**项目级**的，不分集
        · 四视图        —— v5 **没有**四视图概念（一张参考图）
        · 生成         —— v5 **没有单资产重生成**；这里真调后端，但作用域是
                          「本类全部资产」（`POST /assets/batch-generate-image`）
      这些控件对应的字段（`state_name` / `voice_mode` / `voice_description` /
      `style_id` / `model_code` / `fourview_image`）**照样随保存送出**，因为
      「v5 到底存不存这个键」的**判据只有后端那一份**（`webwrite._STATE_WRITABLE`）：
      前端自己钉一张"我认为不支持"的清单，等 v5 真支持了某一项时，那张清单就变成
      **另一处静默失配**。⇒ 每次保存都按响应里的 `ignored` **逐字**显示（键名不改写），
      对应输入同时置灰/只读并写明原因（**可见文案**，不只是 title 悬浮）。
      ⚠️ 送出去的 `model_code` 取的是**界面上当前选中的厂商**（旧版写死
      `'agnes-image'` ⇒ 与选择器显示的不是同一个值 = 存了个谁都不会读的假值）。
   --------------------------------------------------------------------------
   ★ 另三条是 2026-10 对照 `v5/webwrite.py`（`_STATE_WRITABLE` + `save_asset_state`
     约 181-240 行）**逐字核对**后补的实现，专治「假报成功」：

   ③ **`applied` 的键是注册表字段名，不是请求里的键。** 请求送 `description`，
      `applied` 回的是 `identity`（映射的目标键）。直接把原始键念出来会让人以为
      后端多写了一个字段，所以按 `APPLIED_CN` 翻成人话。
   ④ **空串既不进 `applied` 也不进 `ignored`**（后端 `if v and ...` 直接跳过）；
      "值与盘上相同"同样不进 `applied`。⇒ `applied` 为空有两种真相，这里分开说；
      **一次都没写进去时不弹「已保存」**（旧版弹了 —— 那正是本项目最忌的假报成功）。
   ⑤ **`identity` 是 v5 的唯一身份来源**（会注入每一镜提示词）。它在后端挂在
      `states[0].identity`（`webmap.asset_refs`），`types.ts` 里声明的
      `AssetItem.identity` 后端**不产**、只当兜底读。界面上显式标出来，空着给红字警告。
   ========================================================================== */

import { useState } from 'react';
import { Api } from '../api';
import { Store } from '../store';
import type { AssetItem, Project } from '../types';
import { Icon } from './Icons';
import { Drawer } from './Overlay';
import { GenOverlay } from '../lib/genOverlay';
import { VendorSelect, Vendor } from './VendorSelect';

export type AssetKind = 'character' | 'scene' | 'prop';

/** v5 暂无对应语义的控件 → 统一渲染成 disabled + 说明，避免"点了没反应"。 */
const NO_V5 = {
  voice: 'v5 没有单资产音色（音频口径是项目级的 brief.audio_mode）',
  voice_desc: '同上：音色描述 v5 无落点',
  apply_all: 'v5 每个资产只有一份形象，没有"多形象"可应用',
  episode: 'v5 的资产是项目级的，不分集',
  fourview: 'v5 没有四视图概念（只产出一张参考图）',
  upload: 'v5 的资产图由媒体链产出，不支持上传',
};

const bucketKey = (k: AssetKind) => (k === 'character' ? 'characters' : k === 'scene' ? 'scenes' : 'props');
const kindCn = (k: AssetKind) => (k === 'character' ? '角色' : k === 'scene' ? '场景' : '道具');

/**
 * 后端 `save_asset_state` 的响应体（`v5/webwrite.py:190-240` 逐字核对）：
 * `{ id, name, type, applied{}, ignored[], note }`。
 * ⚠️ **没有 `ok` 这个键**，别去找它：外层信封是 `{ code, message, data }`
 * （`webmap.envelope`），`code !== '000000'` 时 `api.ts` 的 `http()` **已经抛了**
 * ⇒ 走到这里的对象只有内层那一份。判成功与否只能看 `applied` 是否非空（见 ④）。
 */
type SaveResp = {
  applied?: Record<string, unknown>;
  ignored?: string[];
  note?: string;
  id?: string;
  name?: string;
  type?: string;
};

/** `add_asset` 的响应：`{ name, type, total, identity_chars }`（identity 长度是后端算的）。 */
type AddResp = { name?: string; type?: string; total?: number; identity_chars?: number };

/** `applied` 回的是**注册表字段名**（请求里的 `description` 落到 `identity`）。 */
const APPLIED_CN: Record<string, string> = {
  name: '名称',
  identity: '外观描述（identity）',
};

/**
 * `ignored` 里可能出现的键 → 为什么 v5 没落点。
 * ★ 键名一律**逐字显示**（说明只是补充）：人要能对得上后端到底回了什么。
 */
const IGNORED_CN: Record<string, string> = {
  state_name: 'v5 每个资产只有一份形象（恒「基础形象」），不会产生新形象',
  voice_mode: 'v5 没有 per-asset 音色（音频口径在项目级 brief.audio_mode）',
  voice_description: '同上：音色描述在 v5 没有落点',
  style_id: '画风是**项目级**的（brief.pack），v5 没有 per-asset 画风',
  model_code: '模型由 v5 配置决定，不接受 per-asset 覆盖',
  fourview_image: 'v5 只产一张参考图，没有四视图',
};

/** 抽屉里那些「v5 没有对应存储」的输入 —— 不用等后端回 ignored 就该让人看见。 */
const NA_FIELDS: { field: string; why: string }[] = [
  { field: '形象名称（state_name）', why: NO_V5.apply_all },
  { field: '声音 / 音色描述（voice_mode / voice_description）', why: NO_V5.voice },
  { field: '应用到所有形象', why: NO_V5.apply_all },
  { field: '剧集', why: NO_V5.episode },
  { field: '四视图（fourview_image）', why: NO_V5.fourview },
  { field: '画风（style_id）/ 模型（model_code）', why: '画风是项目级的（brief.pack）、模型由 v5 配置决定 —— v5 没有 per-asset 落点' },
  { field: '上传 / ＋', why: NO_V5.upload },
];

export function AssetDrawer({ pid, kind, assetId, refId, onClose, onSaved }: {
  pid: string;
  kind: AssetKind;
  /** 空 = 新建模式 */
  assetId: string | null;
  refId?: string | null;
  onClose: () => void;
  onSaved?: () => void;
}) {
  const p = Store.getProject(pid) as Project | null;
  const list = (p && p.assets ? ((p.assets as unknown as Record<string, AssetItem[]>)[bucketKey(kind)] || []) : []);
  const asset = assetId ? (list.find((x) => x.id === assetId) || null) : null;
  const isNew = !asset;
  const state = isNew
    ? { state_name: '基础形象', image: '', identity: '', ref_id: '' }
    : ((refId ? (asset.states || []).find((s) => s.ref_id === refId) : null)
      || (asset.states || [])[0]
      || { state_name: '基础形象', image: '' });

  const [name, setName] = useState(asset ? asset.name : '');
  /**
   * ★ 盘上真正生效的**身份描述**：后端挂在 `states[0].identity`
   * （`webmap.asset_refs` 的 state 里，见 webmap.py:936），
   * 而 `types.ts` 声明的 `AssetItem.identity` **后端不产** —— 只当兜底读，
   * 免得"以为读到了、其实是 undefined"这种静默失配再犯一次。
   */
  const identityOnDisk = String(
    (state as { identity?: string; description?: string }).identity
    || (asset ? asset.identity : '')
    || (state as { description?: string }).description
    || '',
  ).trim();
  const nameOnDisk = asset ? asset.name : '';
  const [desc, setDesc] = useState(identityOnDisk);
  const [busy, setBusy] = useState('');
  // 后端如实回报的「v5 没有存储」的字段，逐字显示（不只在 toast 里一闪而过）
  const [ignoredKeys, setIgnoredKeys] = useState<string[]>([]);
  const [note, setNote] = useState(
    isNew ? '' : '⚠️ 在 v5 里，外观描述（identity）会**注入每一镜提示词**；留空则模型每镜自己编长相。',
  );

  if (!p) return null;
  if (!isNew && !asset) return null;

  const label = kindCn(kind);
  const dis = (reason: string) => ({ disabled: true, title: reason + '（v5 暂无此语义）' });

  /**
   * 「applied 为空」的**两种**真相（后端只回一个空对象，分不出是哪种 ⇒ 前端用
   * 自己知道的"盘上原值"来分辨）。★ 别说成「保存成功」。
   */
  const noChangeReason = (descText: string, nm: string): string => {
    const descSame = descText === identityOnDisk;
    const nameSame = nm === nameOnDisk;
    if (!descText) {
      return '外观描述是**空串** —— 后端 `if v and ...` 会直接跳过它，'
        + '既不写进 applied 也不写进 ignored。'
        + (nameSame ? '名称本来也没变 ⇒ 这次什么都没落盘。' : '');
    }
    if (descSame && nameSame) {
      return '名称与外观描述都和**盘上的值一模一样** ⇒ 没有任何差异可写（不是失败，是没有改动）。';
    }
    return '后端确认收到，但没有任何字段被判定为"有改动"（值与盘上一致，或为空串被跳过）。';
  };

  const save = async () => {
    const nm = name.trim();
    if (!nm) { Store.toast('请填写名称'); return; }
    const descText = desc.trim();
    setBusy('保存中…');
    setIgnoredKeys([]);
    let keepOpen = false;                  // ★ 需要人读的东西（no-op / ignored 清单）别关抽屉
    try {
      if (isNew) {
        // ★ **先建资产**（真落盘：POST /projects/{pid}/assets），identity 一起带上
        const r = (await Api.addAsset(pid, kind, nm, { identity: descText })) as AddResp | null;
        const chars = (r && typeof r.identity_chars === 'number') ? r.identity_chars : descText.length;
        if (!chars) {
          // 创建确实成功了，但**没有身份**= 后续每镜各编一张脸；如实报，不弹纯「已创建」
          Store.toast('已创建「' + nm + '」，但 identity 为空 ⇒ 模型每镜自己编长相'
            + '（请打开这个资产补外观描述）', 'error');
        } else {
          Store.toast('已创建「' + nm + '」（identity ' + chars + ' 字，会进每一镜提示词）', 'ok');
        }
      } else {
        /**
         * ★ 请求体 = 「v5 真会落地的两个键」+「Pavo 有、v5 没有对应存储的那些输入」。
         *   后面这组**照样送**，唯一理由是：后端会原样回一张 `ignored` 清单，
         *   而"哪些键没落点"的判据归它管（`webwrite._STATE_WRITABLE`）——
         *   前端自己写死一张不支持清单，v5 一旦支持其中一项就成了**假新闻**。
         *   ⚠️ `model_code` 取**界面当前选中的厂商**（旧版写死 `'agnes-image'`，
         *   与选择器显示的值不是同一个 ⇒ 送出去一个谁都不会读的假值）。
         */
        const body = {
          name: nm, description: descText,
          state_name: state.state_name || '基础形象',
          voice_mode: 'ai_auto', voice_description: '',
          style_id: p.style?.style_id || '', model_code: Vendor.pick('image') || 'agnes-image',
          fourview_image: { url: '', thumbnail_url: '' },
        };
        const r = (await Api.saveAssetState(pid, kind, assetId as string, body)) as SaveResp | null;
        const appliedKeys = Object.keys((r && r.applied) || {});
        const ignored = ((r && r.ignored) || []).map(String);
        setIgnoredKeys(ignored);
        if (appliedKeys.length) {
          const touched = appliedKeys.map((k) => APPLIED_CN[k] || k);
          setNote('已写入：' + touched.join('、')
            + (r && r.note ? '\n（后端原话）' + r.note : ''));
          // ★ toast 里也把 ignored 的**键名逐字**列出来（抽屉紧接着就关了，note 来不及读）
          Store.toast('已保存：' + touched.join('、')
            + (ignored.length ? '；v5 未存储（改了也不会生效）：' + ignored.join('、') : ''), 'ok');
        } else {
          // ★ 没有任何字段被写入 —— 绝不弹「已保存」（旧版这里无脑弹，是假报成功）
          const why = noChangeReason(descText, nm);
          keepOpen = true;
          setNote('这次**没有任何字段被写入**。' + why
            + (ignored.length ? '\n下面这些字段 v5 没有对应存储（后端逐字回报）：' + ignored.join('、') : ''));
          Store.toast('未保存：' + why.replace(/\*\*/g, '').replace(/\s+/g, ' ')
            + (ignored.length ? '（v5 未存储：' + ignored.join('、') + '）' : ''), 'error');
        }
      }
      if (!keepOpen) { onClose(); onSaved?.(); }
    } catch (e) {
      // ★ 失败要留在抽屉里让人看见（关掉抽屉 = 把错误吞了）
      setNote('保存失败：' + (e as Error).message);
      Store.toast('保存失败：' + (e as Error).message, 'error');
    } finally { setBusy(''); }
  };

  /** 生成参考图：**真打后端**，但作用域是**本类全部资产**（v5 没有单资产重生成）。 */
  const genImage = async () => {
    if (isNew) { Store.toast('先「创建」再生成图片'); return; }
    const vCode = Vendor.pick('image');
    Store.toast('已提交「' + label + '」图片生成（作用于本类全部资产）'
      + (vCode ? '，厂商：' + vCode : '') + '…');
    GenOverlay.show({ title: '正在生成资产图片…', sub: '作用域：本类全部资产' });
    GenOverlay.update({ sub: '已提交，等待后端…' });
    try {
      const st = await Api.runTask(
        () => Api.generateAssetImages(pid, [kind], Vendor.payload('image')) as Promise<{ run_id?: string }>,
        {
          onTick: (s) => GenOverlay.update({ sub: '生成中… ' + ((s && s.status) || 'running') }),
          refresh: async () => {
            const proj = await Api.getProgress(pid);
            Store.upsertProject(proj);
            return true;
          },
        },
      );
      onSaved?.();
      Store.toast(st.status === 'ok' ? '生成完成，已刷新' : '生成未成功（' + st.status + '）',
        st.status === 'ok' ? 'ok' : 'error');
    } catch (e) {
      Store.toast('生成失败：' + (e as Error).message, 'error');
    } finally { GenOverlay.hide(); }
  };

  const paneNa = 'v5 没有四视图概念';
  const paneFoot = (na: boolean) => (
    <>
      {na ? null : <VendorSelect kind="image" cls="ad-vendor" />}
      <button
        type="button" className="btn btn--primary btn--xs"
        disabled={na || !!busy}
        title={na ? 'v5 暂无此语义' : '生成（作用域：本类全部资产）'}
        onClick={() => { if (na) { Store.toast(NO_V5.fourview); return; } genImage(); }}
      >{Icon.refresh(14)} 生成</button>
      {na ? null : (
        <button type="button" className="icon-btn" {...dis(NO_V5.upload)}>{Icon.plus(18)}</button>
      )}
    </>
  );

  return (
    <Drawer onClose={onClose} body={
      <>
      <div className="ov-drawer-head">
        <button type="button" className="icon-btn" onClick={onClose}>{Icon.back(22)}</button>
        <span style={{ fontSize: 15, fontWeight: 500 }}>
          {label}信息{isNew ? ' · 新增' : ''}
        </span>
      </div>
      <div className="ov-drawer-body">
        <aside className="ad-side">
          <div className="ad-tabs">
            <button type="button" className="ad-tab is-active">{state.state_name || '基础形象'}</button>
          </div>
          <div className="ad-form">
            <div className="ad-field">
              <label>名称</label>
              <input className="ad-input" value={name} onChange={(e) => setName(e.target.value)}
                placeholder={'例如：' + (kind === 'character' ? '纸扎匠' : kind === 'scene' ? '纸扎铺' : '十个纸人')} />
            </div>
            <div className="ad-field ad-field--na" title={NO_V5.apply_all + '（v5 暂无此语义）'}>
              <label>形象名称 <span className="ad-na-tag">v5 不存储</span></label>
              {/* ★ 只读 + **可见**说明（旧版只有 title 悬浮，鼠标不移上去什么都不知道） */}
              <input className="ad-input" value={state.state_name || '基础形象'} readOnly disabled
                title={NO_V5.apply_all + '（v5 暂无此语义）'} />
              <span className="hint">{NO_V5.apply_all}</span>
            </div>
            {kind === 'character' ? (
              <>
                <div className="ad-field ad-field--na" title={NO_V5.voice + '（v5 暂无此语义）'}>
                  <label>声音 <span className="ad-na-tag">v5 不存储</span></label>
                  <button type="button" className="ad-select" disabled
                    title={NO_V5.voice + '（v5 暂无此语义）'}>
                    AI 自动适配 <span className="ov-menu-right">›</span>
                  </button>
                  <span className="hint">{NO_V5.voice}</span>
                </div>
                <div className="ad-field ad-field--na" title={NO_V5.voice_desc + '（v5 暂无此语义）'}>
                  <label>音色描述 <span className="ad-na-tag">v5 不存储</span></label>
                  <textarea className="ad-area" rows={3} placeholder={NO_V5.voice_desc} disabled />
                  <span className="hint">{NO_V5.voice_desc}</span>
                </div>
              </>
            ) : null}
            <label className="ad-check ad-field--na" title={NO_V5.apply_all + '（v5 暂无此语义）'}>
              <input type="checkbox" disabled /> 应用到所有形象
              <span className="ad-na-tag">v5 不存储</span>
            </label>
            <div className="ad-field ad-field--na" title={NO_V5.episode + '（v5 暂无此语义）'}>
              <label>剧集 <span className="ad-na-tag">v5 不存储</span></label>
              <input className="ad-input" value="全部（项目级）" readOnly disabled />
              <span className="hint">{NO_V5.episode}</span>
            </div>
            <div className="ad-field">
              <label>
                外观描述
                {/* ★ identity 是 v5 的**唯一身份来源**，界面上必须点出来 ——
                    它和一句"描述"不是一个分量：它会被注入**每一镜**的提示词。 */}
                <span className="badge badge--warn">identity · v5 唯一身份来源</span>
                <span className="muted small">（注入每一镜提示词）</span>
              </label>
              <textarea
                className="ad-area" rows={9}
                placeholder="写清外观：脸型/发型/服装/材质。此文案会进每一镜提示词；留空则模型每镜自己编长相，同一个人会长成 18 个人。"
                value={desc} onChange={(e) => setDesc(e.target.value)}
              />
              <div className="ad-count"><span>{desc.length}</span> 字</div>
              {!desc.trim() ? (
                <div className="small" style={{ color: 'var(--warn)' }}>
                  ⚠️ 留空 = 这个{label}在 v5 里没有身份锚点：同一个人在 18 个镜头里会长成
                  18 个人（问题要到成片才暴露）。而且空串不会被保存
                  —— 后端 `if v and …` 直接跳过它，既不写进 applied 也不报忽略。
                </div>
              ) : null}
              {identityOnDisk && desc.trim() !== identityOnDisk ? (
                <div className="muted small">
                  盘上现值（{identityOnDisk.length} 字）：{identityOnDisk.slice(0, 80)}
                  {identityOnDisk.length > 80 ? '…' : ''}
                </div>
              ) : null}
            </div>
            <button type="button" className="btn btn--primary btn--sm ad-save"
              disabled={!!busy} onClick={save}>
              {isNew ? '创建' : '保存'}
            </button>
            <div className="ad-note">{note}</div>
            {ignoredKeys.length ? (
              // 后端回报的 `ignored` 必须**逐字**列出来（旧版只在 toast 里给一个数字 N，
              // 而文件头注释一直声称"这里把它显示出来"—— 注释大于实现）。
              <div className="ad-na-banner">
                <span>
                  后端如实回报了 <b>{ignoredKeys.length}</b> 个「v5 没有对应存储」的字段
                  —— 改它们<b>不会生效</b>（键名按响应原文列出）：
                </span>
                {ignoredKeys.map((k) => (
                  <div key={k} className="mt8">
                    <span className="ad-na-tag">{k}</span>
                    <span className="small"> {IGNORED_CN[k] || 'v5 无对应存储（后端原样回报，说明未内置）'}</span>
                  </div>
                ))}
              </div>
            ) : null}
            <div className="ad-na-banner">
              <span>
                下面这些输入 v5 <b>没有对应存储</b>，已经置灰/只读 —— 改它们<b>不会生效</b>。
                这份是<b>前端的预判</b>（只用来解释控件为什么禁用）；
                <b>判据以后端逐字回报的 `ignored` 为准</b> —— 点一次「保存」就会出现在上方。
              </span>
              {NA_FIELDS.map((f) => (
                <div key={f.field} className="mt8">
                  <span className="ad-na-tag">{f.field}</span>
                  <span className="small"> {f.why}</span>
                </div>
              ))}
            </div>
          </div>
        </aside>

        <main className="ad-main">
          <div className="ad-title">
            {asset ? asset.name : '未命名'} · {state.state_name || '基础形象'}
          </div>
          <div className="ad-na-banner">
            <span>
              右栏在 v5 侧只产出一张参考图；<b>四视图</b>没有对应语义（已置灰）。
              该项目的资产图由「生成全部图片」统一产出 —— v5 <b>没有单资产重生成</b>。
            </span>
            <div className="mt8 small">
              ★ 参考图读的是 <b>states[0].image</b>（后端 `webmap.asset_refs` 明确给的别名）；
              类型里那个 <b>main_image</b> 后端<b>不产</b>，谁去读它谁就永远显示「未生成」。
              旁边的厂商下拉只决定<b>本次生成请求</b>的 `image_vendor`，
              它<b>不是</b>资产属性 —— v5 没有 per-asset 画风/模型可存。
            </div>
          </div>
          <div className="ad-grid">
            {(['main_image', 'fourview_image'] as const).map((key) => {
              const na = key === 'fourview_image';
              const img = na ? '' : (state as { image?: string }).image;
              return (
                <section key={key} className={'ad-pane' + (na ? ' ad-pane--na' : '')} data-pane={key}>
                  <div className="ad-pane-head">
                    <span className="ad-pane-title">{na ? '四视图（v5 暂无）' : '参考图'}</span>
                    {na ? <span className="ad-na-tag">v5 暂无</span> : null}
                  </div>
                  <div className="ad-ph">
                    {img
                      ? <img src={img} alt="" />
                      : <span className="muted small">{na ? paneNa : '尚未生成'}</span>}
                  </div>
                  <div className="ad-pane-foot">
                    {busy && !na ? <span className="muted small">{busy}</span> : paneFoot(na)}
                  </div>
                  <div className="ad-hist">
                    <span className="muted small">
                      {na ? '—' : (img ? '当前：参考图 1 张' : '暂无')}
                    </span>
                  </div>
                </section>
              );
            })}
          </div>
        </main>
      </div>
      </>
    } />
  );
}
