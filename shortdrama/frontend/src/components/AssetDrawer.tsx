/* ==========================================================================
   src/components/AssetDrawer.tsx —— 角色/场景/道具信息面板（全屏抽屉）
   --------------------------------------------------------------------------
   复刻线上点击资产形象后打开的抽屉。结构：
     左栏  名称 / 形象名称 / 声音 / 音色描述 / 应用到所有形象 / 剧集 / 描述 / [保存]
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
      `state_name` 也会被送达，但 `webwrite.save_asset_state` 会如实回一张
      `ignored` 清单（这里把它显示出来）。
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
  const [desc, setDesc] = useState(String(
    (state as { identity?: string; description?: string }).identity
    || (state as { description?: string }).description || '',
  ));
  const [busy, setBusy] = useState('');
  const [note, setNote] = useState(
    isNew ? '' : '⚠️ 在 v5 里，外观描述会**注入每一镜提示词**；留空则模型每镜自己编长相。',
  );

  if (!p) return null;
  if (!isNew && !asset) return null;

  const label = kindCn(kind);
  const dis = (reason: string) => ({ disabled: true, title: reason + '（v5 暂无此语义）' });

  const save = async () => {
    const nm = name.trim();
    if (!nm) { Store.toast('请填写名称'); return; }
    const descText = desc.trim();
    setBusy('保存中…');
    try {
      if (isNew) {
        // ★ **先建资产**（真落盘：POST /projects/{pid}/assets），identity 一起带上
        await Api.addAsset(pid, kind, nm, { identity: descText });
        Store.toast('已创建「' + nm + '」', 'ok');
      } else {
        const body = {
          name: nm, description: descText,
          state_name: state.state_name || '基础形象',
          // 下面这些 v5 没有对应语义 —— 仍送过去，让后端**如实回一张 ignored 清单**
          voice_mode: 'ai_auto', voice_description: '',
          style_id: p.style?.style_id || '', model_code: 'agnes-image',
          fourview_image: { url: '', thumbnail_url: '' },
        };
        const r = await Api.saveAssetState(pid, kind, assetId as string, body) as {
          applied?: Record<string, unknown>; ignored?: string[];
        };
        const applied = Object.keys((r && r.applied) || {});
        const ignored = (r && r.ignored) || [];
        Store.toast('已保存' + (applied.length ? '：' + applied.join('、') : '')
          + (ignored.length ? '（v5 忽略了 ' + ignored.length + ' 个字段）' : ''), 'ok');
      }
      onClose();
      onSaved?.();
    } catch (e) {
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
              <label>形象名称</label>
              <input className="ad-input" defaultValue={state.state_name || '基础形象'}
                disabled={true} title={NO_V5.apply_all + '（v5 暂无此语义）'} />
            </div>
            {kind === 'character' ? (
              <>
                <div className="ad-field ad-field--na" title={NO_V5.voice + '（v5 暂无此语义）'}>
                  <label>声音</label>
                  <button type="button" className="ad-select" disabled>AI 自动适配 <span className="ov-menu-right">›</span></button>
                </div>
                <div className="ad-field ad-field--na" title={NO_V5.voice_desc + '（v5 暂无此语义）'}>
                  <label>音色描述</label>
                  <textarea className="ad-area" rows={3} placeholder="v5 暂不支持" disabled />
                </div>
              </>
            ) : null}
            <label className="ad-check ad-field--na" title={NO_V5.apply_all + '（v5 暂无此语义）'}>
              <input type="checkbox" disabled /> 应用到所有形象
            </label>
            <div className="ad-field ad-field--na" title={NO_V5.episode + '（v5 暂无此语义）'}>
              <label>剧集</label>
              <input className="ad-input" defaultValue="全部（项目级）" disabled />
            </div>
            <div className="ad-field">
              <label>描述 <span className="muted small">（会注入每一镜提示词）</span></label>
              <textarea
                className="ad-area" rows={9}
                placeholder="写清外观：脸型/发型/服装/材质。此文案会进每一镜提示词；留空则模型每镜自己编长相，同一个人会长成 18 个人。"
                value={desc} onChange={(e) => setDesc(e.target.value)}
              />
              <div className="ad-count"><span>{desc.length}</span> 字</div>
            </div>
            <button type="button" className="btn btn--primary btn--sm ad-save"
              disabled={!!busy} onClick={save}>
              {isNew ? '创建' : '保存'}
            </button>
            <div className="ad-note">{note}</div>
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
