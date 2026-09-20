#!/usr/bin/env bash
# six-winters（《接风》）全链路编排。
#
# 为什么要串起来跑：两件事必须**严格串行**——前一个项目的媒体链还在烧 Agnes 配额时
# 并行起第二个项目会撞 429、也会抢 dev server 的 worker 槽位。
#
# v2（2026-09-13 修订，针对首次失败）的三处修正：
#   ① **失败检测**：创作链跑完必须逐个核对 7 个角色产物，缺任何一个就**不启动媒体链**
#      并明确记 failure。v1 无条件接着跑媒体链，还把结果写成"全链路完成"——
#      实际那 1 秒就因 storyboard gate 退出，日志完全误导。
#   ② **媒体链结果检测**：以 media.log 里是否出现 `RESULT:` 为成功判据。
#   ③ **--fresh**：重跑前归档上一次的创作链产物（保留 brief/assets/images）。
#      不复用旧产物是有原因的：物化守卫看到盘上文件就判 complete，旧分镜会被当成
#      已完成 → 重跑空转。（`guards.stash_artifacts` 是运行期机制，这里是编排层兜底。）
#
# 用法：
#   bash scripts/run_six_winters.sh            # 续跑（复用已有产物）
#   bash scripts/run_six_winters.sh --fresh    # 归档旧产物后从零重跑
set -u
export PATH="/usr/bin:/bin:$PATH"
cd /c/Users/Administrator/Downloads/test/shortdrama || exit 1

P=six-winters
L="projects/$P"
LOG="$L/run.log"
ROLES="worldbuilder assetdesigner plotdesigner scriptwriter dialogue scenedesigner reviewer"
mkdir -p "$L"

log() { echo "[$(date +%m-%d\ %H:%M:%S)] $*" >> "$LOG"; }

log "=== six-winters 全链路启动（参数：${1:-续跑}）==="

# ── 0) 可选：归档上一次的创作链产物 ─────────────────────────────────────────
if [ "${1:-}" = "--fresh" ]; then
    .venv/Scripts/python.exe - "$P" <<'PY' >> "$LOG" 2>&1
import shutil, sys, time
from pathlib import Path
root = Path("projects") / sys.argv[1]
keep = {"brief.json", "assets.json", "images", "run.log", "chain.log",
        "media.log", "dev.log"}
bak = root / ".rerun_backup" / time.strftime("%m%d-%H%M%S")
moved = []
for p in sorted(root.iterdir()):
    if p.name in keep or p.name.startswith(".rerun_backup"):
        continue
    bak.mkdir(parents=True, exist_ok=True)
    shutil.move(str(p), str(bak / p.name))
    moved.append(p.name)
print("archived ->", moved)
PY
    log "已归档旧产物（保留 brief/assets/images）"
fi

# ── 1) 归档 .langgraph_api：防陈旧 run 复活（占槽位 / 撞 429 / 按过期意图推进）──
if [ -d .langgraph_api ]; then
    mv .langgraph_api ".langgraph_api.bak-$(date +%m%d%H%M)"
    log "已归档 .langgraph_api"
fi

# ── 2) 起 dev server（项目是**编译期绑定**，必须在这里指定）────────────────────
log "启动 dev server（SHORTDRAMA_V5_PROJECT=$P）"
SHORTDRAMA_V5_PROJECT=$P \
SHORTDRAMA_STUDIO_PACK=shortdrama \
SHORTDRAMA_OPEN_CHAIN=1 \
NO_PROXY="127.0.0.1,localhost,agnes-ai.com,agnes-ai.space" no_proxy="127.0.0.1,localhost,agnes-ai.com,agnes-ai.space" \
    .venv/Scripts/langgraph.exe dev --config v5/langgraph.json \
    --host 127.0.0.1 --port 2024 --no-browser > "$L/dev.log" 2>&1 &

OK=0
for i in $(seq 1 90); do
    if curl -s --noproxy '*' http://127.0.0.1:2024/ok 2>/dev/null | grep -q true; then
        log "dev server 就绪（第 ${i} 次探测）"
        OK=1
        break
    fi
    sleep 2
done
if [ "$OK" != "1" ]; then
    log "!! dev server 未就绪 → 终止（见 dev.log）"
    sleep 21600
    exit 1
fi

# ── 3) 创作链（supervisor，7 角色）────────────────────────────────────────────
log "创作链开始"
SHORTDRAMA_OPEN_CHAIN=1 NO_PROXY="127.0.0.1,localhost,agnes-ai.com,agnes-ai.space" no_proxy="127.0.0.1,localhost,agnes-ai.com,agnes-ai.space" \
    .venv/Scripts/python.exe -u scripts/drive_chain.py "$P" --timeout 5400 \
    > "$L/chain.log" 2>&1
log "创作链进程结束"

# ★ 失败检测：逐个核对角色产物目录里是否有 .md
MISSING=""
for r in $ROLES; do
    ls "$L/$r"/*.md >/dev/null 2>&1 || MISSING="$MISSING $r"
done
if [ -n "$MISSING" ]; then
    log "!! 创作链失败，缺角色产物：$MISSING"
    log "!! 不启动媒体链（否则只会被 media_gate / storyboard gate 拦下空转）"
    log "!! 提示：若是 reviewer 缺失，先看 dev.log 里是否 GraphRecursionError"
    sleep 21600
    exit 1
fi
log "创作链产物齐全：$ROLES"

# ── 4) 媒体链（静帧 → 静帧QC → 视频 → clipqc → 拼接）─────────────────────────
log "媒体链开始"
SHORTDRAMA_OPEN_CHAIN=1 NO_PROXY="127.0.0.1,localhost,agnes-ai.com,agnes-ai.space" no_proxy="127.0.0.1,localhost,agnes-ai.com,agnes-ai.space" \
    .venv/Scripts/python.exe -u -m v5.series "$P" --resume-media \
    > "$L/media.log" 2>&1

if grep -q "RESULT:" "$L/media.log"; then
    log "媒体链成功：$(grep -m1 'RESULT:' "$L/media.log")"
else
    log "!! 媒体链未产出 RESULT —— 失败。最后一条有效日志："
    grep -v "trace=\|multipart\|LangSmithRateLimit" "$L/media.log" | tail -3 >> "$LOG" 2>&1
fi
log "=== 全链路结束 ==="

# ── 5) 保活：dev server 需常驻（沙箱会回收命令进程树）；也给人工观察留窗口 ──────
sleep 21600
