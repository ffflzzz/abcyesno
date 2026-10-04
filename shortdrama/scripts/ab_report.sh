#!/usr/bin/env bash
# 三臂对照的数字账：一张表说清"每臂实际发了什么单、出来多长的片"。
#
# 为什么需要它而不只看视频：三臂的差别首先是**下单结构**（一条请求装几镜、
# 带几张图、锚帧是真末帧还是静帧兜底），这些不数出来就只能靠感觉猜；
# 而"静帧张数三臂相同"这一行是控制变量成立的证据——不相同就说明测的不是档位。
#
# 用法：bash scripts/ab_report.sh <母项目名> [--ep 1]
set -u
cd "$(dirname "$0")/.." || exit 1
M="${1:?用法：ab_report.sh <母项目名> [--ep N]}"; shift
EP=1
while [ $# -gt 0 ]; do case "$1" in --ep) EP="$2"; shift 2 ;; *) shift ;; esac; done

for MODE in reference pack mixed; do
  D="projects/$M-$MODE/media/ep$EP"
  L="projects/$M-$MODE/render_$MODE.log"
  echo "==================== $MODE ===================="
  NS=$(ls "$D/stills/"*.jpg 2>/dev/null | wc -l)
  echo "静帧（三臂应相同）：$NS 张"
  if [ -f "$D/video_jobs.json" ]; then
    PYTHONUTF8=1 .venv/Scripts/python.exe -c "
import json,pathlib,collections
d=json.loads(pathlib.Path('$D/video_jobs.json').read_text(encoding='utf-8'))
st=collections.Counter(str(v.get('state')) for v in d.values())
k=collections.Counter(str(v.get('first_frame_kind')) for v in d.values())
multi=[ (n, len(v.get('shots') or [])) for n,v in d.items() if len(v.get('shots') or [])>1 ]
print('请求条数 %d（状态 %s）' % (len(d), dict(st)))
print('图的用法 %s' % dict(k))
print('一条请求装 2 镜以上的：%d 条 %s' % (len(multi), multi[:6]))
"
  else
    echo "任务账：还没有 video_jobs.json"
  fi
  # pack 臂的接续锚来源：AGENTS 明令「看到 静帧兜底 就说明有问题」
  if [ -f "$L" ]; then
    echo "锚帧来源统计：$(grep -o '接续锚=[^ ]*' "$L" 2>/dev/null | sort | uniq -c | tr '\n' ' ')"
    echo "失败/超窗：$(grep -cE 'FAILED|未在轮询窗口内完成|队列持续满' "$L" 2>/dev/null) 行"
    echo "缺镜/不拼接：$(grep -cE '缺镜|不拼接' "$L" 2>/dev/null) 行"
  else
    echo "渲染日志：$L 不存在"
  fi
  F="$D/episode_final.mp4"
  if [ -f "$F" ]; then
    echo "成片：$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$F" | tr -d '\n') 秒，$(stat -c %s "$F") 字节"
  else
    echo "成片：⛔ 无"
  fi
done
echo
echo "并排片：tmp/CMP_${M}_ep${EP}.mp4"
echo "接触表：tmp/FRAMES_${M}_ep${EP}/"
