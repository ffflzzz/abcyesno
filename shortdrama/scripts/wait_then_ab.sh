#!/usr/bin/env bash
# 等媒体链跑完，再跑静默镜 A/B 音频测试。
#
# 为什么必须等：视频提交有供应商侧 1rpm 闸门。两条链并行时，
# A/B 会持续撞 429（2026-09-13 实测：双双提交失败）。
set -u
export PATH="/usr/bin:/bin:$PATH"
cd /c/Users/Administrator/Downloads/test/shortdrama || exit 1

L=projects/morning-stall
echo "[wait] $(date +%H:%M:%S) 等待媒体链结束（最多 40 分钟）..."
for i in $(seq 1 160); do
  if grep -q "全链路结束" "$L/run.log" 2>/dev/null; then
    echo "[wait] $(date +%H:%M:%S) 媒体链已结束"; break
  fi
  sleep 15
done

if ! grep -q "全链路结束" "$L/run.log" 2>/dev/null; then
  echo "[wait] !! 超时仍未结束，仍然开始测试（闸门可能被占，脚本自带 429 重试）"
fi

echo "[ab] $(date +%H:%M:%S) 开始 A/B 测试"
./.venv/Scripts/python.exe -u scripts/ab_silent_audio.py morning-stall LN01
echo "[ab] $(date +%H:%M:%S) 测试结束"
sleep 3600
