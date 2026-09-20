/* ==========================================================================
   src/pages/PlayletList.tsx —— 短剧工作台首页（对应线上 /playlet/list）
   --------------------------------------------------------------------------
   从 `web/assets/js/views/playlet-list.js` 移植。**行为逐条对齐**，
   凡是旧版注释里带「★ / ⚠️」的判据都保留了 —— 那些都是踩出来的，不是风格问题。

   移植手法上的一个关键差异（值得单独说）：
     旧版用**一个视图级 `ui` 对象 + 手工 `rerender()`（整页 innerHTML 重刷）**，
     所以它必须处处小心"别整页重渲染，否则输入框会丢焦点/丢字符"
     （见旧 `syncStylePreview` 的注释）。React 里这就是普通 `useState`，
     **焦点丢失这个类别的问题不存在**。
   ========================================================================== */

import { useCallback, useEffect, useRef, useState } from 'react';
import { Api } from '../api';
import { Router } from '../router';
import { Store, useStore } from '../store';
import type { Project } from '../types';
import { Icon } from '../components/Icons';
import { ConfirmModal, Menu, Modal, PromptModal } from '../components/Overlay';

type ListTab = 'mine' | 'featured';
type FeaturedState = 'idle' | 'loading' | 'ok' | 'error';

/** 视频比例与集数是**离线模板**才会用到的东西；v5 目前只支持 9:16。 */
const RATIOS = ['9:16'];
const EPISODE_CHOICES = [1, 2, 3, 5, 10];
const PASTE_MAX = 100000;
const IDEA_MAX = 10000;

export function PlayletList() {
  const { styles, projects } = useStore();

  const [createTab, setCreateTab] = useState<'upload' | 'ai'>('upload');
  const [listTab, setListTab] = useState<ListTab>('mine');
  // 三段式：已粘贴文本 → 选风格 → 剧本解析
  const [draft, setDraft] = useState<string | null>(null);
  const [styleCat, setStyleCat] = useState('');
  const [styleCode, setStyleCode] = useState('');
  const [styleName, setStyleName] = useState('');
  // AI 生成剧本：一个输入框 + 三项设置
  const [aiIdea, setAiIdea] = useState('');
  const [aiStyle, setAiStyle] = useState('');
  const [aiEpisodes, setAiEpisodes] = useState(1);
  const [aiBusy, setAiBusy] = useState(false);
  // 精选项目：**视图局部**，不进 Store（见下方 loadFeatured 的说明）
  const [featured, setFeatured] = useState<Project[] | null>(null);
  const [featuredState, setFeaturedState] = useState<FeaturedState>('idle');
  const [featuredError, setFeaturedError] = useState('');
  const [menu, setMenu] = useState<{ p: Project; rect: DOMRect; el: HTMLElement } | null>(null);
  const [modal, setModal] = useState<
    | { kind: 'paste' }
    | { kind: 'rename'; p: Project }
    | { kind: 'delete'; p: Project }
    | null
  >(null);

  const featuredInflight = useRef(false);
  const fileRef = useRef<HTMLInputElement | null>(null);
  const isHttp = Api.driver === 'http';

  /**
   * 拉「精选项目」（服务端 `GET /projects?is_demo=true`）。
   *
   * **每次都拉，不做长期缓存**：它是服务端的运营数据（谁被标成精选由
   * `projects/featured.json` 决定），本地存一份只会过期 ——
   * 本项目在"厂商列表 / 风格库"上已经吃过同型亏（缓存盖掉后端的新列表 = 静默偏差）。
   * 只挡**并发重复请求**。当前 tab 上再点一次 = 手动刷新。
   */
  const loadFeatured = useCallback(() => {
    if (featuredInflight.current) return;
    if (Api.driver !== 'http') {
      // 本地模式没有服务端 → 不发请求，落到「本地离线取不到」那一档文案
      setFeatured(null);
      setFeaturedState('idle');
      setFeaturedError('');
      return;
    }
    featuredInflight.current = true;
    setFeaturedState('loading');
    setFeaturedError('');
    Api.listProjects(1, 100, true)
      .then(
        (d) => { setFeatured((d && d.list) || []); setFeaturedState('ok'); },
        (err: Error & { fromBackend?: boolean; status?: number }) => {
          setFeatured(null);
          setFeaturedState('error');
          // 「后端答复了」与「根本联系不上」必须说不同的话（见 api.ts 的 http()）
          setFeaturedError(err.fromBackend
            ? ((err.status ? err.status + ' ' : '') + (err.message || ''))
            : '后端未连通');
        },
      )
      .finally(() => { featuredInflight.current = false; });
  }, []);

  const rerenderHint = useStore();      // 订阅一次即可（onChange 由 React 负责重渲染）
  void rerenderHint;

  /* ------------------------------------------------------------ 打开项目 */

  /**
   * 打开项目 —— **唯一入口**，空标识一律拒绝。
   * ⛔ 为什么不直接 `Router.go('/playlet/review/' + id)`：
   *   JS 里 `'...' + null` 会得到**字面串** `'...null'` → hash 变成
   *   `#/playlet/review/null` → 后端稳定 404「项目不存在：null」，
   *   而界面把它显示成"后端未连通"（**这句报错本身也误导**）。
   *   旧版实测（2026-09-17）：shim 日志里每次导航都跟着两条
   *   `GET /projects/null/progress → 404` ⇒ 这里做**防御性拦截**。
   */
  const openProject = useCallback((id: string | undefined | null) => {
    const bad = (v: unknown) => !v || v === 'null' || v === 'undefined';
    if (bad(id)) {
      console.warn('[playlet] 拒绝打开项目：标识为空', id);
      Store.toast('项目标识缺失，无法打开（请刷新页面重试）', 'error');
      return false;
    }
    Router.go('/playlet/review/' + id);
    return true;
  }, []);

  /* ------------------------------------------------------------ 动作 */

  const doRename = (p: Project, name: string) => {
    setModal(null);
    Api.renameProject(p.id, name).then(
      () => { Store.renameLocal(p.id, name); Store.toast('已重命名', 'ok'); },
      (e: Error) => Store.toast('重命名失败：' + e.message, 'error'),
    );
  };

  const doDelete = (p: Project) => {
    setModal(null);
    Api.deleteProject(p.id).then(
      () => { Store.removeLocal(p.id); Store.toast('已删除'); },
      (e: Error) => Store.toast('删除失败：' + e.message, 'error'),
    );
  };

  /**
   * 导出工程 JSON / 全部导出 / 导入工程。
   *
   * ⚠️ **导入在 http 驱动下如实拒绝**：旧版 `importJSON` 是往**本地库**写
   * （离线版的数据层）；现在的数据在后端，而**后端没有"导入工程"这个接口**。
   * 假装导入成功（写进内存）会在刷新后静默消失 —— 那是本项目最忌的静默偏差。
   */
  const downloadJson = (text: string, name: string) => {
    const blob = new Blob([text], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = name;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  };

  const onExportAll = () => {
    if (!projects.length) { Store.toast('还没有项目可导出'); return; }
    downloadJson(JSON.stringify(projects, null, 2), '短剧工程-全部.json');
    Store.toast('已导出 ' + projects.length + ' 个工程', 'ok');
  };

  const onExportOne = (p: Project) => {
    downloadJson(JSON.stringify(p, null, 2), p.name + '.json');
    Store.toast('已导出「' + p.name + '」', 'ok');
  };

  const onImport = () => {
    Store.toast(isHttp
      ? '导入工程需要后端支持：当前后端没有这个接口（旧版是写本地库）'
      : '导入工程尚未移植（离线数据层未移植）', 'error');
  };

  const onPickFile = (f: File) => {
    const rd = new FileReader();
    rd.onload = () => {
      const name = f.name.replace(/\.[^.]+$/, '') || '未命名短剧';
      Api.createByPaste(name, String(rd.result || '')).then(
        (p) => { Store.toast('项目已创建', 'ok'); openProject(p && p.id); },
        (e: Error) => Store.toast('创建失败：' + e.message, 'error'),
      );
    };
    rd.readAsText(f, 'utf-8');
  };

  const onParse = () => {
    if (!draft || !styleName) { Store.toast('分析剧本前请选择风格'); return; }
    Store.toast('解析中…');
    setAiBusy(true);
    Api.createByParse(draft, styleCode)
      .then((p) => {
        const a = (p as unknown as { analyze?: { characters?: number; scenes?: number; props?: number; note?: string } }).analyze;
        Store.toast(a
          ? (a.note
            ? '解析完成：' + a.characters + ' 角色。' + a.note
            : '解析完成：' + a.characters + ' 角色 / ' + a.scenes + ' 场景 / ' + a.props + ' 道具')
          : '解析请求已提交', 'ok');
        setDraft(null);
        setStyleName('');
        openProject(p && p.id);
      })
      .catch((e: Error) => Store.toast('解析失败：' + e.message, 'error'))
      .finally(() => setAiBusy(false));
  };

  const onAiGenerate = () => {
    const idea = aiIdea.trim();
    if (!idea) { Store.toast('请先写下你的故事创意'); return; }
    if (!isHttp) { Store.toast('AI 创作需要后端（当前是离线模式）', 'error'); return; }
    setAiBusy(true);
    Store.toast('正在由 AI 创作剧本，约 20–40 秒…');
    Api.createByAI({
      idea, style_code: aiStyle || (styles[0] && styles[0].code) || '',
      ratio: RATIOS[0], episodes: aiEpisodes,
    })
      .then((p) => {
        Store.toast('已创作：《' + (String((p as unknown as { topic?: string }).topic) || (p && p.id)) + '》', 'ok');
        setAiIdea('');
        openProject(p && p.id);
      })
      .catch((e: Error) => Store.toast('创作失败：' + e.message, 'error'))
      .finally(() => setAiBusy(false));
  };

  /* ------------------------------------------------------------ 空态文案 */

  /**
   * 空态文案 —— **按真实原因分档**。
   *
   * 为什么值得单独一个函数（旧版原注释照搬）：这里原来只有一句
   *   「精选项目需要联网获取，离线版不提供」
   * 而当时精选列表是**硬编码空**的（`slice(0, 0)`），所以这句话在**联网状态下也照显示**
   * —— 它把"这个功能还没接线"说成了"你处于离线模式"。归因错误的提示比没有提示更糟：
   * 实测它直接把人带偏到"我是不是装了两个前端版本"。
   *
   * 四种真实情况，四种说法（顺序即优先级）：
   *   ① 我的项目为空      → 提示去创建
   *   ② 本地离线模式      → 精选来自服务端，本地取不到（**只有这一档才能说"离线"**）
   *   ③ http 且拉取失败   → 说失败，并说明"我的项目不受影响"
   *   ④ http 且成功但 0 条 → 后端确实没有精选，**不是离线、也不是失败**
   */
  const emptyText = (): string => {
    if (listTab === 'mine') return '还没有项目，先在上方创建一个吧';
    if (!isHttp) return '精选项目由服务端维护，本地离线模式取不到（当前数据只存在本机浏览器）';
    if (featuredState === 'loading') return '正在拉取精选项目…';
    if (featuredState === 'error') {
      return '精选项目拉取失败：' + (featuredError || '后端未响应') + '（我的项目不受影响，仍是服务端数据）';
    }
    if (featuredState === 'ok') return '后端暂无标记为精选的项目（服务端 is_demo=true 返回 0 条）';
    return '精选项目尚未拉取 —— 点上方「精选项目」重新获取';
  };

  /* ------------------------------------------------------------ 子渲染 */

  /**
   * 风格预览卡：**真实样张** + 该包会注入提示词的文案 + 样张出处。
   * 两样东西都是**真的**：`cover_url` 是该类型包真实项目的静帧；
   * `visual_style` 是该包**真正会注入每一镜提示词**的那段（不是宣传语）。
   * 没有样张的包**如实显示「暂无样张」**，不编。
   */
  const stylePreview = (code: string) => {
    const s = styles.find((x) => x.code === code) || styles[0] || null;
    if (!s) return null;
    let meta = '';
    if (s.cover_url) {
      meta = '样张来自《' + (s.sample_topic || s.sample_project || '') + '》' + (s.sample_shot || '');
      if (s.sample_stills) meta += '（该包共 ' + s.sample_stills + ' 张静帧）';
    } else if (!isHttp) {
      meta = '离线模式：缩略图需要连上后端';
    }
    return (
      <div className="style-prev">
        <div className="style-prev-thumb">
          {s.cover_url
            ? <img src={s.cover_url} alt="" loading="lazy" />
            : <div className="empty">暂无样张<br />该包还没有项目<br />出过静帧</div>}
        </div>
        <div className="style-prev-body">
          <div className="style-prev-name">{s.name} <span className="muted small">{s.code}</span></div>
          {s.visual_style ? <div className="style-prev-desc">{s.visual_style}</div> : null}
          {meta ? <div className="style-prev-meta">{meta}</div> : null}
        </div>
      </div>
    );
  };

  const uploadPane = (
    <div className="dropzone" id="dz">
      <div className="dz-actions">
        <button type="button" className="btn btn--primary btn--sm" onClick={() => fileRef.current?.click()}>
          {Icon.upload(16)} 上传我的剧本
        </button>
        <button type="button" className="btn btn--sm" onClick={() => setModal({ kind: 'paste' })}>
          {Icon.edit(16)} 粘贴剧本
        </button>
      </div>
      <div className="dz-hint">支持 txt/docx 格式，剧本字数不超过10万字，可拖拽至此处上传</div>
      <input
        ref={fileRef} type="file" accept=".txt,.docx,.md" className="hidden"
        onChange={(e) => { const f = e.target.files?.[0]; if (f) onPickFile(f); e.target.value = ''; }}
      />
    </div>
  );

  const pastedPane = () => {
    const groups: string[] = [];
    styles.forEach((s) => { const g = s.group || '其他'; if (!groups.includes(g)) groups.push(g); });
    const cat = groups.includes(styleCat) ? styleCat : (groups[0] || '其他');
    const inCat = styles.filter((s) => (s.group || '其他') === cat);
    return (
      <>
        <div className="pasted-box">
          <div className="pasted-head">
            <span className="badge badge--done">已粘贴文本</span>
            <button type="button" className="btn btn--ghost btn--xs"
              onClick={() => { setDraft(null); setStyleName(''); setStyleCode(''); }}>重新粘贴</button>
          </div>
          <div className="pasted-text">{String(draft || '').slice(0, 160)}…</div>
          <div className="pasted-foot">
            <button type="button" className="btn btn--sm">
              <span className="muted">风格库</span> {styleName || '请选择风格'}
            </button>
            <span className="muted small">9:16</span>
            <button type="button" className="btn btn--primary btn--sm"
              disabled={!styleName || aiBusy} onClick={onParse}>剧本解析</button>
          </div>
          <div className="muted small mt8">{styleName ? '' : '分析剧本前请选择风格'}</div>
        </div>
        <div className="pasted-cats">
          {groups.map((c) => (
            <button key={c} type="button"
              className={'asset-tab' + (cat === c ? ' is-active' : '')}
              onClick={() => setStyleCat(c)}>{c}</button>
          ))}
        </div>
        <div className="pasted-styles">
          {inCat.map((s) => (
            <button key={s.code} type="button"
              className={'pasted-style' + (styleName === s.name ? ' is-active' : '')}
              onClick={() => {
                setStyleName(s.name);
                // 类型包的 **code** 才是后端要的
                setStyleCode(s.code || s.name);
                Store.toast('已选择风格：' + s.name, 'ok');
              }}>{s.name}</button>
          ))}
        </div>
        {stylePreview(styleCode || (styles.find((s) => s.name === styleName)?.code || ''))}
      </>
    );
  };

  const aiPane = () => {
    const cur = (aiStyle && styles.some((s) => s.code === aiStyle))
      ? aiStyle : (styles[0]?.code || '');
    return (
      <div className="col gap12">
        <div className="dropzone" style={{ padding: 20 }}>
          <textarea
            id="ai-idea" className="paste-area" maxLength={IDEA_MAX}
            style={{ minHeight: 200 }}
            placeholder="请输入你的故事创意，可包含故事背景、主角设定、剧情走向、结局等"
            value={aiIdea} onChange={(e) => setAiIdea(e.target.value)}
          />
          <div className="muted small" style={{ textAlign: 'right', marginTop: -8 }}>
            <span id="ai-count">{aiIdea.length}</span>/{IDEA_MAX}
          </div>
          <div className="pasted-foot" style={{ marginTop: 12 }}>
            <select id="ai-style" className="btn btn--sm" title="类型包决定审美与角色契约"
              value={cur} onChange={(e) => setAiStyle(e.target.value)}>
              {styles.length
                ? styles.map((s) => <option key={s.code} value={s.code}>风格库 · {s.name}</option>)
                : <option value="">（风格库未加载）</option>}
            </select>
            <select id="ai-ratio" className="btn btn--sm" title="v5 目前只支持 9:16" defaultValue={RATIOS[0]}>
              {RATIOS.map((r) => <option key={r} value={r}>{r}</option>)}
            </select>
            <select id="ai-episodes" className="btn btn--sm" title="集数"
              value={aiEpisodes} onChange={(e) => setAiEpisodes(parseInt(e.target.value, 10) || 1)}>
              {EPISODE_CHOICES.map((n) => <option key={n} value={n}>{n}集</option>)}
            </select>
            <button type="button" className="btn btn--primary btn--sm"
              disabled={!aiIdea.trim() || aiBusy} onClick={onAiGenerate}>
              {aiBusy ? '创作中…' : '开始创作'}
            </button>
          </div>
          {stylePreview(cur)}
          <div className="muted small mt8">
            {isHttp
              ? '由 AI 生成四幕结构的 brief（约 20–40 秒）；剧本与分镜由创作链后续产出'
              : '离线模式：AI 创作需要后端 —— 这里暂不提供本地模板'}
          </div>
        </div>
      </div>
    );
  };

  const projectCard = (p: Project) => {
    const eps = p.episodes || [];
    const firstSeg = eps[0]?.storyboard?.segments?.find((s) => s.keyframe);
    const cover = p.cover || firstSeg?.keyframe || '';
    return (
      <div key={p.id} className="project-card" data-project={p.id}>
        <div className="project-cover" onClick={() => openProject(p.id)}>
          {cover
            ? <img src={cover} alt="" loading="lazy" />
            : <span className="muted small">暂无封面</span>}
        </div>
        <div className="project-meta">
          <div className="flex1" onClick={() => openProject(p.id)}>
            <h3>{p.name}</h3>
            <p className="project-sub">{eps.length}集<span className="divider" />{p.created_at}</p>
          </div>
          <button type="button" className="icon-btn" aria-label="编辑项目名称"
            onClick={(e) => {
              e.stopPropagation();
              const el = e.currentTarget as HTMLElement;
              setMenu(menu && menu.p.id === p.id ? null : { p, rect: el.getBoundingClientRect(), el });
            }}>
            {Icon.ellipsis(20)}
          </button>
        </div>
      </div>
    );
  };

  /** 列表页自己的水合：进页面时拉一次（`mine` 那批由 App 的统一水合负责）。 */
  useEffect(() => {
    if (listTab === 'featured') loadFeatured();
  }, [listTab, loadFeatured]);

  const list = listTab === 'mine' ? projects : (featured || []);

  return (
    <div className="content content--wide">
      <div className="hero">
        <h1>AI剧本创作</h1>
        <p>用AI创作你的下一部爆款短剧</p>
      </div>
      <div className="create-box">
        <div className="create-tabs">
          <button type="button" className={'create-tab' + (createTab === 'upload' ? ' is-active' : '')}
            onClick={() => setCreateTab('upload')}>{Icon.upload(16)} 上传我的剧本</button>
          <button type="button" className={'create-tab' + (createTab === 'ai' ? ' is-active' : '')}
            onClick={() => setCreateTab('ai')}>{Icon.inspiration(16)} AI生成剧本</button>
        </div>
        <div className="create-body">
          {draft ? pastedPane() : (createTab === 'upload' ? uploadPane : aiPane())}
        </div>
      </div>

      <section className="section-gap" style={{ marginTop: 48 }}>
        <div className="section-head">
          <div className="section-tabs">
            <button type="button" className={'section-tab' + (listTab === 'mine' ? ' is-active' : '')}
              onClick={() => setListTab('mine')}>我的项目</button>
            <button type="button" className={'section-tab' + (listTab === 'featured' ? ' is-active' : '')}
              onClick={() => setListTab('featured')}>精选项目</button>
          </div>
          <div className="row gap12">
            <button type="button" className="btn btn--ghost btn--sm" onClick={onImport}>导入工程</button>
            <button type="button" className="btn btn--ghost btn--sm" onClick={onExportAll}>全部导出</button>
            <button type="button" className="btn btn--sm"
              onClick={() => Store.toast('管理：可直接在卡片菜单里重命名/删除')}>管理</button>
          </div>
        </div>
        <div className="project-grid">
          {list.length
            ? list.map(projectCard)
            : <div className="empty">{Icon.empty(48)}<div>{emptyText()}</div></div>}
        </div>
      </section>

      {menu ? (
        <Menu
          anchor={menu.rect}
          anchorEl={menu.el}
          onClose={() => setMenu(null)}
          items={[
            { label: '打开项目', action: () => openProject(menu.p.id) },
            { label: '重命名', action: () => setModal({ kind: 'rename', p: menu.p }) },
            { label: '导出工程 JSON', action: () => onExportOne(menu.p) },
            { label: '删除项目', danger: true, action: () => setModal({ kind: 'delete', p: menu.p }) },
          ]}
        />
      ) : null}

      {modal?.kind === 'paste' ? (
        <PromptModal
          title="粘贴剧本" confirmText="完成" textarea max={PASTE_MAX}
          placeholder="在这里粘贴剧本"
          onClose={() => setModal(null)}
          onConfirm={(v) => {
            setDraft(v);
            setStyleName('');
            setStyleCode('');
            setModal(null);
            Store.toast('已粘贴文本，请选择风格', 'ok');
          }}
        />
      ) : null}

      {modal?.kind === 'rename' ? (
        <PromptModal
          title="重命名项目" value={modal.p.name} max={40} placeholder="项目名称" confirmText="确认"
          onClose={() => setModal(null)}
          onConfirm={(v) => doRename(modal.p, v)}
        />
      ) : null}

      {modal?.kind === 'delete' ? (
        <ConfirmModal
          title="删除项目" danger confirmText="删除"
          text={'确定删除项目「' + modal.p.name + '」？删除后无法恢复。'}
          onClose={() => setModal(null)}
          onOk={() => doDelete(modal.p)}
        />
      ) : null}

      {aiBusy ? (
        <Modal
          title="正在处理…" width={420}
          hint="这一步会真的调用后端（生成 brief / 解析剧本），通常 20–40 秒。"
          confirmText="知道了"
          onClose={() => setAiBusy(false)}
          onConfirm={() => setAiBusy(false)}
          body={<div className="ov-text">请求已提交，请稍候。关闭这个提示不会取消请求。</div>}
        />
      ) : null}
    </div>
  );
}
