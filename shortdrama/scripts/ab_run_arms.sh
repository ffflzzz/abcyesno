#!/usr/bin/env bash
# 三臂视频渲染发射器：同一份静帧，只换档位。
#
# 为什么写成脚本而不是手敲三遍：本仓库最贵的事故之一是**起链漏带参数**
# （画幅 / 镜数上限 / 提交超时 / 视频档）——漏一个就是整轮白烧。参数集中一处，
# 三个臂天然同构。
#
# 用法：
#   bash scripts/ab_run_arms.sh <母项目名> [--ep 1] [--arms reference,pack,mixed] [--serial]
# 前置：母项目已出好静帧（scripts/ab_arm.py 会校验并克隆）。
set -u
cd "$(dirname "$0")/.." || exit 1

MASTER=""
EP=1
ARMS="reference,pack,mixed"
PARALLEL=1
while [ $# -gt 0 ]; do
  case "$1" in
    --ep) EP="$2"; shift 2 ;;
    --arms) ARMS="$2"; shift 2 ;;
    --serial) PARALLEL=0; shift ;;
    *) MASTER="$1"; shift ;;
  esac
done
[ -n "$MASTER" ] || { echo "用法：ab_run_arms.sh <母项目名> [--ep N] [--arms a,b,c] [--serial]"; exit 2; }

# 出厂项（settings.env 只有打包版会读，CLI 必须自己带）
BASE_ENV=(PYTHONUTF8=1
  SHORTDRAMA_ASPECT=9:16
  SHORTDRAMA_STILL_RATIO=9:16
  AGNES_VIDEO_MAX_SHOTS=60
  AGNES_VIDEO_MAX_SECONDS=12
  SHORTDRAMA_VIDEO_BGM=0
  SHORTDRAMA_VIDEO_KEY_ROTATE=1
  SHORTDRAMA_QC_WORKERS=8
  SHORTDRAMA_OPEN_CHAIN=1
  # 两臂共用同一份静帧 ⇒ 静帧质检必须关（否则某一臂就地重画掉对照基准）
  SHORTDRAMA_STILL_QC=0
  # 成片抽帧重拍也必须关（否则 reference/mixed 拿到 pack 拿不到的自动重拍次数）
  SHORTDRAMA_CLIP_QC=0)

PY=".venv/Scripts/python.exe"
PIDS=""
IFS=',' read -ra LIST <<< "$ARMS"
for MODE in "${LIST[@]}"; do
  ARM="${MASTER}-${MODE}"
  if [ ! -d "projects/${ARM}" ]; then
    echo "[ab] ⛔ 臂目录不存在：projects/${ARM} —— 先跑 scripts/ab_arm.py ${MASTER} --arms ${ARMS} --ep ${EP}"
    exit 2
  fi
  N=$(ls "projects/${ARM}/media/ep${EP}/stills/"*.jpg 2>/dev/null | wc -l)
  if [ "$N" -eq 0 ]; then
    echo "[ab] ⛔ 臂 ${ARM} 没有静帧 ⇒ 母项目第 ${EP} 集还没出静帧"
    exit 2
  fi
  if [ -f "projects/${ARM}/media/ep${EP}/video_jobs.json" ]; then
    echo "[ab] ⛔ 臂 ${ARM} 里还有 video_jobs.json —— 那是上一轮的渲染记账，"
    echo "     留着会让本臂直接复用旧成片（假对照）。删掉臂目录重新克隆。"
    exit 2
  fi
  LOG="projects/${ARM}/render_${MODE}.log"
  EX=(SHORTDRAMA_VIDEO_MODE="$MODE")
  [ "$MODE" = "pack" ] && EX+=(SHORTDRAMA_VIDEO_SUBMIT_TIMEOUT=180)  # 多图提交 60s 会误判失败
  echo "[ab] ${ARM}：静帧 ${N} 张，档位 ${MODE}，日志 ${LOG}"
  env "${BASE_ENV[@]}" "${EX[@]}" "$PY" -u -m v5.series "$ARM" --resume-media --ep "$EP" \
      > "$LOG" 2>&1 &
  PIDS="$PIDS $!"
  [ "$PARALLEL" = "0" ] && { wait $!; tail -3 "$LOG"; }
done
if [ "$PARALLEL" = "1" ]; then
  echo "[ab] 三臂并行已发出：$PIDS"
  for p in $PIDS; do wait "$p"; done
fi
echo "[ab] 全部臂结束。成片应在 projects/<臂>/media/ep${EP}/episode_final.mp4"
for MODE in "${LIST[@]}"; do
  F="projects/${MASTER}-${MODE}/media/ep${EP}/episode_final.mp4"
  [ -f "$F" ] && echo "  ✅ ${MODE}: $(stat -c %s "$F") 字节  ${F}" || echo "  ⛔ ${MODE}: 无成片（看 projects/${MASTER}-${MODE}/render_${MODE}.log）"
done
