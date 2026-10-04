#!/usr/bin/env bash
# 三臂对照的编排器：创作链收工 → 母项目静帧 → 克隆三臂 → 三臂并行渲染 → 并排对比片。
#
# 为什么写成一段后台脚本：这一段里有三个 30-90 分钟的等待，逐段等人来点是浪费。
# 但每一步的**终止条件绑在真实终态上**（不是宽松 grep），失败就停下并写清停在哪，
# 绝不"日志全绿就当成功"。
#
# 判据一份不重造：分镜合格与否由系统门说（`--stills-only` 会过 STORYBOARD-REJECT
# 与渲染门），本脚本只负责按顺序发起与如实记录，不自己再判一遍。
set -u
cd "$(dirname "$0")/.." || exit 1

M="${1:-madfate-abc-1005}"
EP="${2:-1}"
PY=".venv/Scripts/python.exe"
LOG="projects/$M/orchestrate.log"
say() { echo "[orch $(date +%H:%M:%S)] $*" | tee -a "$LOG"; }
step() { say "── $*"; }

BASE_ENV=(PYTHONUTF8=1 SHORTDRAMA_ASPECT=9:16 SHORTDRAMA_STILL_RATIO=9:16
  AGNES_VIDEO_MAX_SHOTS=60 AGNES_VIDEO_MAX_SECONDS=12 SHORTDRAMA_VIDEO_BGM=0
  SHORTDRAMA_VIDEO_KEY_ROTATE=1 SHORTDRAMA_QC_WORKERS=8 SHORTDRAMA_OPEN_CHAIN=1)

# ── 1) 等创作链终态：run.log 出现 --chain-only 的收工串（唯一终态标记）──
step "等创作链收工（哨兵：run.log 里的『--chain-only：到此结束』，上限 100 分钟）"
END=$(( $(date +%s) + 6000 ))
SEEN=0
while [ "$(date +%s)" -lt "$END" ]; do
  if grep -q -- "--chain-only：到此结束" "projects/$M/run.log" 2>/dev/null; then SEEN=1; break; fi
  sleep 30
done
if [ "$SEEN" != 1 ]; then
  say "⛔ 等满 100 分钟没看到创作链收工串 —— **停止**（不猜它跑完了没）。看 projects/$M/chain.log"
  echo "ORCH_FAIL stage=chain_wait" | tee -a "$LOG"; exit 1
fi
say "创作链已收工"

# ── 2) 三臂形态预检（离线、不花钱）：pack 会不会一镜一组、mixed 有没有承接镜 ──
step "分镜预检"
"$PY" tmp/preflight_arms_1005.py "$M" --ep "$EP" >> "$LOG" 2>&1
say "预检 rc=$?（详情见 orchestrate.log）"

# ── 3) 母项目出静帧（这一轮静帧质检**开**，一次把硬伤画干净，三臂共用）──
step "母项目出第 $EP 集静帧（静帧 QC 开）"
env "${BASE_ENV[@]}" SHORTDRAMA_STILL_QC=1 "$PY" -u -m v5.series "$M" \
    --resume-media --stills-only --ep "$EP" >> "projects/$M/stills_master.log" 2>&1
RC=$?
N=$(ls "projects/$M/media/ep$EP/stills/"*.jpg 2>/dev/null | wc -l)
say "静帧阶段 rc=$RC，盘上静帧 $N 张"
if [ "$RC" != 0 ] || [ "$N" -lt 1 ]; then
  say "⛔ 静帧没出够（$N 张）—— **停止**，不克隆、不烧三臂视频额度。"
  say "   原因在 projects/$M/stills_master.log 末尾（多半是渲染门或分镜契约门拦下）"
  tail -20 "projects/$M/stills_master.log" | tee -a "$LOG"
  echo "ORCH_FAIL stage=stills" | tee -a "$LOG"; exit 1
fi

# ── 4) 克隆三臂 ──
step "克隆三臂（共用这 $N 张静帧）"
"$PY" scripts/ab_arm.py "$M" --arms reference,pack,mixed --ep "$EP" 2>&1 | tee -a "$LOG"
[ "${PIPESTATUS[0]}" = 0 ] || { echo "ORCH_FAIL stage=clone" | tee -a "$LOG"; exit 1; }

# ── 5) 三臂并行渲染 ──
step "三臂并行渲染（静帧 QC / 成片重拍 在两臂都关，见 ab_run_arms.sh）"
bash scripts/ab_run_arms.sh "$M" --ep "$EP" >> "$LOG" 2>&1
say "渲染阶段 rc=$?"

# ── 6) 并排对比片 ──
step "出并排对比片"
bash scripts/ab_compare.sh "$M" --ep "$EP" --out "tmp/CMP_${M}_ep${EP}.mp4" >> "$LOG" 2>&1
say "对比片阶段 rc=$?"

for MODE in reference pack mixed; do
  F="projects/$M-$MODE/media/ep$EP/episode_final.mp4"
  [ -f "$F" ] && say "  ✅ $MODE 成片 $(stat -c %s "$F") 字节" || say "  ⛔ $MODE 无成片"
done
echo "ORCH_DONE" | tee -a "$LOG"
