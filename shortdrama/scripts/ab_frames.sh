#!/usr/bin/env bash
# 三臂抽帧台账：每臂一张 1 帧/秒 的接触表 + 一份**按任务表**（不是按成片时间轴）的镜号账。
#
# 为什么镜号必须读 video_jobs.json：reference/mixed 的素材文件叫 LNxx.mp4，
# pack 的叫 packNN.mp4（一条含多镜）——拿成片秒数反推镜号必然错位（本仓库栽过一次）。
# 为什么必须看接触表而不是只看接缝：三臂的差别很多落在**镜内**（打包档一条视频里
# 演两镜的动作，逐镜档不会），只看接缝等于只量了打包档擅长的那一件事。
#
# 用法：bash scripts/ab_frames.sh <母项目名> [--ep 1] [--cols 12]
set -u
cd "$(dirname "$0")/.." || exit 1
M="${1:?用法：ab_frames.sh <母项目名> [--ep N] [--cols 12]}"; shift
EP=1; COLS=12
while [ $# -gt 0 ]; do
  case "$1" in
    --ep) EP="$2"; shift 2 ;;
    --cols) COLS="$2"; shift 2 ;;
    *) shift ;;
  esac
done
OUT="tmp/FRAMES_${M}_ep${EP}"
mkdir -p "$OUT"
for MODE in reference pack mixed; do
  D="projects/$M-$MODE/media/ep$EP"
  F="$D/episode_final.mp4"
  echo "=========== $MODE ==========="
  if [ ! -f "$F" ]; then echo "  ⛔ 无成片：$F"; continue; fi
  echo "  时长/画幅：$(ffprobe -v error -show_entries format=duration:stream=width,height -of csv=p=0 "$F" 2>/dev/null | tr '\n' ' ')"
  echo "  ── 任务账（键=产物名，shots=这条请求装的镜，state）──"
  PYTHONUTF8=1 .venv/Scripts/python.exe -c "
import json,sys,pathlib
p=pathlib.Path('$D/video_jobs.json')
d=json.loads(p.read_text(encoding='utf-8')) if p.exists() else {}
for k,v in d.items():
    print('   %-8s state=%-10s shots=%-28s 秒=%s 图的用法=%s' % (
        k, str(v.get('state')), ','.join(v.get('shots') or [k]),
        v.get('total_seconds') or v.get('seconds') or '?',
        v.get('first_frame_kind') or '?'))
print('   合计 %d 条请求' % len(d))
"
  # 1 帧/秒 → 接触表（每格宽 200）。★ tile 的 layout 必须写完整 `COLSxROWS`：
  #   写成 `12x` 会被判非法（实测 Unable to parse "layout"），空格会自动留白、
  #   帧数不足也在流末冲刷出来（30 秒片进 12x12 照样出一张表，实测 rc=0）。
  ffmpeg -v error -y -i "$F" -vf "fps=1,scale=200:-1,tile=${COLS}x${COLS}" -frames:v 1 \
      "$OUT/${MODE}_sheet.jpg" 2>/dev/null
  [ -f "$OUT/${MODE}_sheet.jpg" ] && echo "  ✅ 接触表：$OUT/${MODE}_sheet.jpg（每格 1 秒，左起第 1 秒）" \
                                 || echo "  ⛔ 接触表没出来（ffmpeg tile 失败）"
done
echo
echo "接触表目录：$OUT"
