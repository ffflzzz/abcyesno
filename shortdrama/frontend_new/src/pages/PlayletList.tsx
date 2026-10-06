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

   2026-10 的三处诚实性修正（判据写在各自行内注释里）：
     · 卡片上的画幅读 **`p.ratio`**（旧版**写死 `9:16`** —— 横屏包会被标成竖屏）；
     · 列表报「共 X 个 / 当前列出 Y 个」并给「加载更多」：旧版一次取 100 条且
       **没有任何分页 UI**，而后端明明回了 `total` ⇒ 项目多于 100 个时**静默截断**；
     · 封面只用 `p.cover`：列表/进度端点上 `ep.storyboard.segments` 是**恒空数组**，
       那条回落是"看着有兜底、其实死路"的假兜底（完整分镜只在 storyboard/detail）。
   ========================================================================== */

import { useCallback, useEffect, useRef, useState } from 'react';
import { Api } from '../api';
import { Router } from '../router';
import { Store, useStore } from '../store';
import type { ApiError, Project } from '../types';
import { Icon } from '../components/Icons';
import { ConfirmModal, Menu, Modal, PromptModal } from '../components/Overlay';
import { packCaps } from '../lib/packcaps';

type ListTab = 'mine' | 'featured';
type FeaturedState = 'idle' | 'loading' | 'ok' | 'error';

/**
 * 画幅档位的**兜底值**（只在后端 `/health` 没拉到时用）。
 *
 * ⚠️ 原先这里是写死的 `const RATIOS = ['9:16']`，注释还写着「v5 目前只支持 9:16」——
 *   那句话**不成立**：`brief.ratio` 是真生效的（`media/runner.py:244` 拿它同时写子进程的
 *   `SHORTDRAMA_STILL_RATIO` 与 `SHORTDRAMA_ASPECT`），而后端把候选发在
 *   `GET /health` 的 `ratio_choices`（单一来源 `config.RATIO_CHOICES`，6 档）。
 *   ⇒ 后果是横屏类型包（`xianxia-vfx-action` 要 16:9）在网页上**建不出对应画幅的项目**，
 *   而界面一处都没说"是前端少了选项"。与 `styles` / `vendors` 同一条教训：
 *   **候选列表不许在前端写死一份**。
 */
const RATIO_FALLBACK = ['9:16'];
const EPISODE_CHOICES = [1, 2, 3, 5, 10];
const PASTE_MAX = 100000;
const IDEA_MAX = 10000;

/**
 * 一次取多少条 —— **必须与水合那次一致**（`api.ts` 的 `hydrate()` 走的是
 * `Api.listProjects(1, 100, false)`，而 api.ts 不在本次改动范围内）。
 * ⚠️ 后端把 page_size 夹在 `max(1, min(200, …))`（`v5/server.py:380`），
 * 所以「加载更多」只能按**同一个 page_size 往后翻页**：中途换数就会整段跳过
 * （水合给了第 1-100 条，我改用 200 去要"第 2 页"= 201-400 ⇒ 101-200 静默丢失）。
 */
const PAGE_SIZE = 100;

/**
 * 读 `GET /projects` 响应里的 `total`（`v5/server.py:384` 逐字核对：
 * `list` / `items` 同值，另有 `total` / `page` / `page_size` / `is_demo`）。
 * ⚠️ `Api.listProjects` 的签名只声明了 `{ list }` ⇒ 总数只能按真实响应体读；
 * 读不到就**如实说「总数未知」**，绝不拿"已经列出的条数"冒充总数
 * —— 那正是静默截断的长相。
 */
function totalOf(d: unknown): number | null {
  const t = (d as { total?: unknown } | null | undefined)?.total;
  const n = typeof t === 'number' ? t : parseInt(String(t ?? ''), 10);
  return Number.isFinite(n) && n >= 0 ? n : null;
}

/** 翻页合并时按 id 去重（页边界重算 / 本地已删过的条目 ⇒ **不重也不漏**）。 */
function uniqById(list: Project[]): Project[] {
  const seen: string[] = [];
  return list.filter((p) => {
    if (seen.includes(p.id)) return false;
    seen.push(p.id);
    return true;
  });
}

/** 「后端答复了」与「根本联系不上」必须说不同的话（见 api.ts 的 `http()`）。 */
function errText(e: unknown): string {
  const er = (e || {}) as ApiError;
  return er.fromBackend
    ? ((er.status ? er.status + ' ' : '') + (er.message || ''))
    : '后端未连通';
}

export function PlayletList() {
  const { styles, projects, health } = useStore();

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
  /**
   * ★ 画幅**必须是受控的**：旧版那个 `<select>` 只有 `defaultValue`（不受控），
   * 而提交时送的是 `RATIOS[0]` —— 界面上选的东西和发给后端的东西不是同一份。
   * 现在只有一个档位所以还没出事，但档位一加立刻就是**静默失配**。
   * （这条路径的 ratio **真的会被后端用掉**：`projects/ai-generate` →
   * `webchain.create_project(ratio=…)` → `brief["ratio"]`；创建后的真值看卡片上的 `p.ratio`。）
   */
  const [aiRatio, setAiRatio] = useState(RATIO_FALLBACK[0]);
  /** 人没手动选过 ⇒ 后端 `/health` 一到就把默认值对齐到 `ratio_default`。 */
  const [ratioTouched, setRatioTouched] = useState(false);
  const [aiBusy, setAiBusy] = useState(false);

  /**
   * 画幅候选 = **后端给的**（`GET /health` 的 `ratio_choices`，见文件头 `RATIO_FALLBACK`）。
   * 拉不到时退回 `9:16` 并在下拉的提示里**如实说**"没拿到后端档位"——
   * 不许让人以为"这个后端只支持 9:16"。
   */
  const ratioChoices = (health && health.ratio_choices && health.ratio_choices.length)
    ? health.ratio_choices : RATIO_FALLBACK;
  useEffect(() => {
    if (ratioTouched || !health || !health.ratio_default) return;
    if (ratioChoices.indexOf(health.ratio_default) >= 0 && aiRatio !== health.ratio_default) {
      setAiRatio(health.ratio_default);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [health, ratioTouched]);
  // 精选项目：**视图局部**，不进 Store（见下方 loadFeatured 的说明）
  const [featured, setFeatured] = useState<Project[] | null>(null);
  const [featuredState, setFeaturedState] = useState<FeaturedState>('idle');
  const [featuredError, setFeaturedError] = useState('');
  /**
   * ★ 分页诚实性：两个 tab 各自的**后端总数**与"总数问不到"的实话。
   * `null` = 没问到 / 响应里根本没有 `total` ⇒ 界面必须说"总数未知"，
   * 而不是把当前列出的条数当成总数（旧版就是这么静默少给数据的）。
   */
  const [mineTotal, setMineTotal] = useState<number | null>(null);
  const [featuredTotal, setFeaturedTotal] = useState<number | null>(null);
  const [mineNote, setMineNote] = useState('');
  const [featuredNote, setFeaturedNote] = useState('');
  const [loadingMore, setLoadingMore] = useState(false);
  /** 删除/新建之后重新问一次总数（不重造数字，只重问）。 */
  const [probeTick, setProbeTick] = useState(0);
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
   *
   * ★ 同一次响应里顺手把 `total` 读走（精选的总数**只问这一次**，不再另发探测请求）。
   */
  const loadFeatured = useCallback(() => {
    if (featuredInflight.current) return;
    if (Api.driver !== 'http') {
      // 本地模式没有服务端 → 不发请求，落到「本地离线取不到」那一档文案
      setFeatured(null);
      setFeaturedState('idle');
      setFeaturedError('');
      setFeaturedTotal(null);
      setFeaturedNote('');
      return;
    }
    featuredInflight.current = true;
    setFeaturedState('loading');
    setFeaturedError('');
    setFeaturedNote('');
    Api.listProjects(1, PAGE_SIZE, true)
      .then(
        (d) => {
          setFeatured((d && d.list) || []);
          const t = totalOf(d);
          setFeaturedTotal(t);
          setFeaturedNote(t == null
            ? '后端这一页没有回 total ⇒ 无法确认是否还有没显示出来的精选项目'
            : '');
          setFeaturedState('ok');
        },
        (err: Error) => {
          setFeatured(null);
          setFeaturedState('error');
          setFeaturedTotal(null);
          // 「后端答复了」与「根本联系不上」必须说不同的话（见 api.ts 的 http()）
          setFeaturedError(errText(err));
        },
      )
      .finally(() => { featuredInflight.current = false; });
  }, []);

  /**
   * ★ 问一次「我的项目」的总数。
   *
   * 为什么要在页面里补这一发请求：列表数据是 App 的水合写进 Store 的，
   * 而水合只留了 `list`（`total` 被丢在 `api.ts` 里，本次不许改那个文件）
   * ⇒ 页面无法从 Store 知道后端到底有多少个项目。
   *
   * ⚠️ 探测**只用与水合相同的 page_size 取第一页，并且不回写 Store**
   * （列表的唯一写入方仍是水合；两处都写 = 同一判据写两份，必漂移）。
   */
  useEffect(() => {
    if (!isHttp || listTab !== 'mine') return;
    let alive = true;
    setMineNote('正在问后端总数…');
    try {
      Api.listProjects(1, PAGE_SIZE, false).then(
        (d) => {
          if (!alive) return;
          const t = totalOf(d);
          setMineTotal(t);
          setMineNote(t == null
            ? '后端这一页没有回 total ⇒ 无法确认是否还有没显示出来的项目'
            : '');
        },
        (e: Error) => {
          if (!alive) return;
          setMineTotal(null);
          setMineNote('项目总数未知：' + errText(e));
        },
      );
    } catch (e) {
      setMineTotal(null);
      setMineNote('项目总数未知：' + errText(e));
    }
    return () => { alive = false; };
  }, [isHttp, listTab, probeTick]);

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
      // ★ 删除会改后端总数 ⇒ 重新问一次（不自己把 total 减一：前端减出来的数
      //   和水合/后端的数迟早对不上，那又是一处静默漂移）
      () => {
        Store.removeLocal(p.id);
        setProbeTick((n) => n + 1);
        Store.toast('已删除');
      },
      (e: Error) => Store.toast('删除失败：' + e.message, 'error'),
    );
  };

  /**
   * ★「加载更多」：按**已列出的条数**推下一页号，取回来**追加合并**。
   *
   * 为什么不记"我翻到第几页"：删掉一个项目后列表少一条，按页数记就会
   * **永远跳过那一格**（少给数据且无声）。按条数推 ⇒ 最坏是多取一页重复，
   * `uniqById` 去掉 ⇒ 不重也不漏。
   *
   * ⚠️ 精选那批是视图局部状态，`setFeatured` 合并；「我的」走 `Store.upsertProjects(arr, false)`
   *   —— 第二参数必须是 `false`：传 `true` 会把前面几页整体抹掉，又回到静默少给。
   */
  const loadMore = () => {
    const mine = listTab === 'mine';
    const shown = mine ? projects.length : (featured ? featured.length : 0);
    const page = Math.floor(shown / PAGE_SIZE) + 1;
    setLoadingMore(true);
    try {
      Api.listProjects(page, PAGE_SIZE, !mine).then(
        (d) => {
          const arr = (d && d.list) || [];
          const t = totalOf(d);
          // 一页取回 0 条却还报着差额 ⇒ 分页与总数对不上，**明说**（别再让人反复点）
          const stall = arr.length
            ? ''
            : ('第 ' + page + ' 页返回 0 条，而后端 total 是 '
              + (t == null ? '未知' : t) + ' 个 ⇒ 分页与总数对不上，请刷新或核对后端');
          if (mine) {
            Store.upsertProjects(arr, false);
            if (t != null) setMineTotal(t);
            setMineNote(stall);
          } else {
            setFeatured((prev) => uniqById((prev || []).concat(arr)));
            if (t != null) setFeaturedTotal(t);
            setFeaturedNote(stall);
          }
        },
        (e: Error) => Store.toast('加载更多失败：' + errText(e), 'error'),
      ).finally(() => setLoadingMore(false));
    } catch (e) {
      setLoadingMore(false);
      Store.toast('加载更多失败：' + ((e as Error).message || String(e)), 'error');
    }
  };

  /**
   * ★ 列表页脚：「共 X 个 · 当前列出 Y 个」+ 差额 + 加载更多按钮。
   * 三种状态都说得出话：总数已知 / 总数未知 / 分页与总数对不上 ——
   * **任何一档都不许安静地少给数据**。
   */
  const listMeta = () => {
    const mine = listTab === 'mine';
    // 精选还没拉到 / 拉失败：空态文案（emptyText）已经说清了，这里不重复一条
    if (!mine && featured == null) return null;
    const shown = mine ? projects.length : (featured ? featured.length : 0);
    if (!isHttp) {
      return (
        <div className="muted small mt8">
          本地模式没有服务端列表，问不到总数（本机当前 {shown} 个）
        </div>
      );
    }
    const total = mine ? mineTotal : featuredTotal;
    const note = mine ? mineNote : featuredNote;
    const hidden = total == null ? 0 : Math.max(0, total - shown);
    const mainText = total == null
      // 问总数还在路上 / 响应里没有 total / 后端未连通 —— note 已经把原因说清了
      ? (note || ('总数未知 · 当前列出 ' + shown + ' 个（后端没有回 total）'))
      : ('共 ' + total + ' 个 · 当前列出 ' + shown + ' 个' + (hidden ? '' : ' · 已全部列出'));
    return (
      <div className="row gap12 mt8" style={{ flexWrap: 'wrap' }}>
        <span className="muted small">{mainText}</span>
        {hidden > 0 ? <span className="badge badge--warn">还有 {hidden} 个没有显示</span> : null}
        {hidden > 0 ? (
          <button type="button" className="btn btn--ghost btn--sm"
            disabled={loadingMore} onClick={loadMore}>
            {loadingMore ? '加载中…' : ('加载更多（每次 ' + PAGE_SIZE + ' 个）')}
          </button>
        ) : null}
        {/* 总数已知却仍有 note ⇒ 是"翻页与总数对不上"那种矛盾，别吞掉 */}
        {total != null && note
          ? <span className="small" style={{ color: 'var(--warn)' }}>{note}</span>
          : null}
      </div>
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
    // ★ 「全部导出」导的只是**已经列出的那批**。后端还有没加载的项目时必须明说，
    //   否则这个文件名（"全部"）本身就是个假报成功。
    const hidden = mineTotal == null ? 0 : Math.max(0, mineTotal - projects.length);
    Store.toast('已导出列出的 ' + projects.length + ' 个工程'
      + (hidden
        ? '（后端共 ' + mineTotal + ' 个，还有 ' + hidden + ' 个不在这个文件里 —— 先点「加载更多」）'
        : mineTotal == null
          ? '（总数未知：没问到后端 total，别当成"全都在这儿"）'
          : ''), 'ok');
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
      // ★ 送的是**界面上选中的那一个**（`aiRatio` 受控），不是写死的 `RATIOS[0]`
      ratio: aiRatio, episodes: aiEpisodes,
    })
      .then((p) => {
        const topic = String((p as unknown as { topic?: string }).topic) || (p && p.id);
        // ★ 回显**后端实际落盘的画幅**（`public_project` 里的 `ratio`），
        //   而不是把界面选中的那个念一遍 —— 两者不一样时后者会把差异盖掉。
        const real = String(p.ratio || '');
        Store.toast('已创作：《' + topic + '》 · 画幅 ' + (real || '后端未回 ratio')
          + (real && real !== aiRatio ? '（与所选 ' + aiRatio + ' 不同，以后端为准）' : ''), 'ok');
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
    const caps = packCaps(s);
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
          {/*
            ★ 选包之前就要看得见**配音模式与参考图策略**（2026-10-02 补）。
            后端 `styles()` 一直给这三项，此前只在向导页显示，新建时看不到 ——
            于是"选完才发现这个包不绑参考图 / 是无声档"。口径收在 `lib/packcaps.ts`
            一处（两个页面各写一份中文标签必然漂移）。
            `has_style_block=false` 只在**确实没有**时提示，不假报。
          */}
          <div className="style-prev-desc">
            {caps.audio}<span className="muted"> · </span>{caps.refs}
          </div>
          {caps.block ? <div className="style-prev-meta" style={{ color: 'var(--warn)' }}>{caps.block}</div> : null}
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
            {/* ★ 旧版这里**写死 `9:16`**。但粘贴这条路径**根本不发送画幅参数**：
                `Api.createByParse(draft, styleCode)` 只发 `content` + `style_code`，
                后端 `POST /projects/paste` 调 `create_project` 时也没传 `ratio`
                ⇒ 真实画幅由 `brief.ratio` / 全局缺省 `config.ASPECT_RATIO` 决定。
                项目还没建出来 ⇒ 这里**没有**"当前画幅"可显示，就不报编出来的数字，
                创建后以项目卡片上的 `p.ratio`（真值）为准。 */}
            <span className="muted small"
              title="粘贴/上传这条路径不发送画幅参数：实际画幅由后端（brief.ratio 或全局缺省）决定">
              画幅：后端决定
            </span>
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
            <select id="ai-ratio" className="btn btn--sm"
              title={health && health.ratio_choices && health.ratio_choices.length
                ? '后端 /health 给的候选档位；会写进 brief.ratio，而这一轮出片**真的按它走**'
                  + '（runner 用它同时设 SHORTDRAMA_STILL_RATIO 与 SHORTDRAMA_ASPECT）'
                : '没拉到后端档位（/health 未连通）—— 这里只列了兜底的 9:16，不代表后端只支持 9:16'}
              value={aiRatio}
              onChange={(e) => { setRatioTouched(true); setAiRatio(e.target.value); }}>
              {ratioChoices.map((r) => <option key={r} value={r}>{r}</option>)}
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
    /**
     * ★ 封面**只读 `p.cover`**。旧代码这里还回落
     *   `eps[0].storyboard.segments.find(s => s.keyframe)?.keyframe` ——
     *   那条回落**永远拿不到东西**：列表/进度端点上后端**恒给空 segments**
     *   （`v5/webmap.py` 的 `_episode_row`，注释里点名就是给 `playlet-list.js` 的
     *   封面回落用的"轻量壳"；完整分镜只在 `GET /episodes/{eid}/storyboard/detail`）。
     *   留着它 = "看着有兜底、其实是死路"，比没有兜底更误导人。
     *   没封面就**如实**显示「暂无封面」，不去编一张图。
     */
    const cover = p.cover || '';
    /**
     * ★ 画幅读 **`p.ratio`**（后端 = `brief.ratio` 或全局缺省 `config.ASPECT_RATIO`）。
     *   旧版在卡片附近**写死 9:16**：横屏的类型包（如 xianxia-vfx-action 是 16:9）
     *   会被标成竖屏 —— 界面上的字与盘上的事实无关，正是本项目最忌的静默失配。
     *   后端没给就显示「画幅未知」，**不拿 9:16 顶替**。
     */
    const ratio = String(p.ratio || '').trim();
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
            <div className="row gap12 mt8">
              {ratio
                ? (
                  <span className="badge badge--done" title="后端 p.ratio（brief.ratio 或全局缺省）">
                    {ratio}
                  </span>
                )
                : (
                  <span className="badge badge--warn" title="后端没给 ratio —— 不拿 9:16 顶替">
                    画幅未知
                  </span>
                )}
            </div>
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
            <button type="button" className="btn btn--ghost btn--sm"
              // ★ 重新问一次总数 / 重拉精选：本地刚建了项目时"共 X 个"会滞后
              onClick={() => {
                if (listTab === 'featured') { loadFeatured(); return; }
                setProbeTick((n) => n + 1);
              }}>
              {listTab === 'featured' ? '重新拉取' : '刷新总数'}
            </button>
            <button type="button" className="btn btn--sm"
              onClick={() => Store.toast('管理：可直接在卡片菜单里重命名/删除')}>管理</button>
          </div>
        </div>
        <div className="project-grid">
          {list.length
            ? list.map(projectCard)
            : <div className="empty">{Icon.empty(48)}<div>{emptyText()}</div></div>}
        </div>
        {/* ★ 分页诚实性：共多少 / 列了多少 / 还差多少，差额摆明了并给「加载更多」 */}
        {listMeta()}
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
