/**
 * Pixa bridge —— 让 shortdrama 的三栏工作室（`frontend_new`）能把这张画布
 * 当**实时视图**用：生产链每出一个资产/静帧/片段，宿主不重载 iframe，
 * 而是把新的节点集 postMessage 进来，这里做**保位合并**。
 *
 * ## 为什么必须走 `setNodes` 而不是 `useCanvasStore.updateProject`
 *
 * `project.tsx` 把 `nodes` 存在**组件本地 state**里，并有一条 effect
 * （`project.tsx:391-394`）在每次渲染时把本地 state **推回** store。
 * 所以从外面调 `updateProject` 会在下一帧被本地 state 覆盖掉 ——
 * 表现是"合并进去了但画面没变"。唯一正确的入口是那两个 setState。
 *
 * ## 为什么**不**自动把节点改动写回分镜表
 *
 * 静帧节点 `s:LNxx` 的 `metadata.prompt` 是**组装后的静帧提示词**
 * （`v5/canvasout.py` 直接取 `stills.json` 的 `prompt`），里面含类型包风格块、
 * 参考图声明、反烧字条款 —— 它**不等于**分镜表的「画面描述」列。
 * 把它写回 `visual` 会把整列污染成一段机器提示词，下一轮静帧就在错误指令上重画。
 * ⇒ 分镜列的编辑走**宿主侧的镜头检查器**（那里读的是表里的真值），
 *   本桥只负责"看得见"和"报个数"。
 *
 * ## 惰性激活
 *
 * 模块加载只挂一个 `message` 监听。**收到宿主第一条 `pixa:merge` 才激活**。
 * 没收到就什么都不发生 ⇒ `/frontend` 那个旧的画布入口（直接开 iframe、
 * 没有宿主）行为与改造前**一字不变**。
 */
import { useEffect, useRef } from "react";
import type { CanvasConnection, CanvasNodeData } from "@/types/canvas";

const HOST_SOURCE = "pixa-host";
const CANVAS_SOURCE = "pixa-canvas";

/** 会被实时刷新的产物字段。布局字段（position/width/height）**不在**这里 ——
 *  人把节点拖到哪，那一格就留在哪。 */
const LIVE_FIELDS = ["content", "prompt", "status", "errorDetails", "seconds", "title"] as const;

type MergePayload = {
    nodes?: CanvasNodeData[];
    connections?: CanvasConnection[];
    title?: string;
};

let active = false;
let lastFingerprint = "";
let hostOrigin = "*";

function fingerprint(nodes: CanvasNodeData[]): string {
    // 只哈希"产物事实"，不含位置 —— 否则人拖一下节点就被当成一次新产出。
    let s = "";
    for (const n of nodes) {
        const m = (n.metadata || {}) as Record<string, unknown>;
        s += n.id + "|" + String(m.content || "") + "|" + String(m.status || "") + "|" + String(m.seconds || "") + ";";
    }
    return String(nodes.length) + ":" + s.length + ":" + hash32(s);
}

function hash32(text: string): number {
    let h = 2166136261;
    for (let i = 0; i < text.length; i += 1) {
        h ^= text.charCodeAt(i);
        h = Math.imul(h, 16777619);
    }
    return h >>> 0;
}

/** 产物事实相同 ⇒ 复用**同一个对象引用**，让 React 跳过这一格的重渲染。
 *  ⚠️ 不做这件事的话，每 3 秒一次合并会让 30 张图全部重新解码一次。 */
function sameFacts(a: CanvasNodeData, b: CanvasNodeData): boolean {
    if (a.title !== b.title || a.width !== b.width || a.height !== b.height) return false;
    const ma = (a.metadata || {}) as Record<string, unknown>;
    const mb = (b.metadata || {}) as Record<string, unknown>;
    return LIVE_FIELDS.every((k) => ma[k] === mb[k]);
}

export function mergeKeepingLayout(
    prev: CanvasNodeData[],
    incoming: CanvasNodeData[],
): CanvasNodeData[] {
    if (!incoming.length) return prev;                 // 空集不许把画布清空
    const prevById = new Map(prev.map((n) => [n.id, n]));
    return incoming.map((n) => {
        const old = prevById.get(n.id);
        if (!old) return n;
        const merged: CanvasNodeData = {
            ...n,
            position: old.position,
            width: old.width,
            height: old.height,
        };
        return sameFacts(old, merged) ? old : merged;
    });
}

export function mergeConnections(
    prev: CanvasConnection[],
    incoming: CanvasConnection[],
): CanvasConnection[] {
    if (!incoming.length) return prev;
    const seen = new Set<string>();
    const out: CanvasConnection[] = [];
    for (const c of incoming) {
        if (seen.has(c.id)) continue;
        seen.add(c.id);
        out.push(c);
    }
    for (const c of prev) {
        if (!seen.has(c.id)) {
            seen.add(c.id);
            out.push(c);
        }
    }
    return out;
}

function post(kind: string, payload: unknown) {
    // ★ `hello` / `ready` **不受 `active` 门限** —— 桥的激活靠画布先自报家门。
    //   第一版把这条也一起挡了，于是：画布等宿主先说话才激活、宿主等画布先打招呼
    //   才推数据 ⇒ **双向死等**，界面永远显示「画布未连接」（实测抓到）。
    if (!active && kind !== "pixa:hello" && kind !== "pixa:ready") return;
    try {
        parent.postMessage({ source: CANVAS_SOURCE, kind, payload }, active ? hostOrigin : "*");
    } catch {
        /* 宿主没了（iframe 被拆）⇒ 静默，桥不该把画布弄崩 */
    }
}

/** 在 `project.tsx` 里挂一次：把两个 setState 交给桥，返回清理函数。 */
export function usePixaBridge(handlers: {
    setNodes: React.Dispatch<React.SetStateAction<CanvasNodeData[]>>;
    setConnections: React.Dispatch<React.SetStateAction<CanvasConnection[]>>;
}) {
    const ref = useRef(handlers);
    ref.current = handlers;
    useEffect(() => {
        const onMessage = (ev: MessageEvent) => {
            const data = ev.data as { source?: string; kind?: string; payload?: MergePayload } | null;
            if (!data || data.source !== HOST_SOURCE) return;
            if (data.kind === "pixa:ping") {
                active = true;
                hostOrigin = ev.origin || "*";
                post("pixa:ready", { projectId: null });
                return;
            }
            if (data.kind !== "pixa:merge" || !data.payload) return;
            active = true;
            hostOrigin = ev.origin || "*";
            const { nodes, connections } = data.payload;
            if (Array.isArray(nodes) && nodes.length) {
                ref.current.setNodes((prev) => mergeKeepingLayout(prev, nodes));
            }
            if (Array.isArray(connections)) {
                ref.current.setConnections((prev) => mergeConnections(prev, connections));
            }
        };
        window.addEventListener("message", onMessage);
        post("pixa:hello", {});
        return () => window.removeEventListener("message", onMessage);
    }, []);
}

/** 每次节点变化报一次数（宿主据此知道"画布活着 + 现在有几格"）。 */
export function usePixaReport(nodes: CanvasNodeData[]) {
    useEffect(() => {
        if (!active) return;
        const fp = fingerprint(nodes);
        if (fp === lastFingerprint) return;
        lastFingerprint = fp;
        post("pixa:nodes", {
            count: nodes.length,
            fingerprint: fp,
            shots: nodes.map((n) => (n.id.startsWith("s:") ? n.id.slice(2) : "")).filter(Boolean),
        });
    }, [nodes]);
}

/** ★ 画布上的**选中**要能被宿主看见 —— 这是"在画布上改分镜"的入口：
 *  人在画布里点一格静帧（节点 id `s:LNxx`），宿主把镜头检查器切到那一镜，
 *  改完写回分镜表。没有这条，选中就困在 iframe 里，检查器只能靠宿主自己的
 *  镜号列表点，那就不叫"在画布上改"了。
 *
 *  ⚠️ 只报 `s:` 前缀的镜节点：资产格（`a:`）与片段组（`p:`）不对应任何一行分镜，
 *  报过去会让检查器停在一个改不动的对象上。多选时报**第一个**（人一次只改一镜）。
 */
export function usePixaSelection(selectedNodeIds: string[] | Set<string>) {
    const last = useRef<string | null>(null);
    useEffect(() => {
        if (!active) return;
        const ids = Array.from(selectedNodeIds as Iterable<string>);
        const shot = (ids.find((id) => id.startsWith("s:")) || "").slice(2);
        if (shot === last.current) return;
        last.current = shot;
        post("pixa:select", { shot });
    }, [selectedNodeIds]);
}
