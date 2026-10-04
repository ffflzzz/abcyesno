#!/usr/bin/env bash
# 三臂并排对比片：左=reference 中=pack 右=mixed，静音（-an），只比画面。
#
# 为什么并排片必须静音：三臂各有一条完整音轨，直接混三路是噪音；台词差异走
# 单独看每一臂的成片，对比片只回答"画面接得上接不上"。
# 为什么顺手打三条时长：三臂成片秒数本身是一项结论（打包档会把秒数压进 12 秒，
# 逐镜档不会），只看并排画面会漏掉它。
#
# 用法：bash scripts/ab_compare.sh <母项目名> [--ep 1] [--out tmp/CMP_xxx.mp4]
set -u
cd "$(dirname "$0")/.." || exit 1
MASTER="${1:?用法：ab_compare.sh <母项目名> [--ep N] [--out 文件]}"; shift
EP=1; OUT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --ep) EP="$2"; shift 2 ;;
    --out) OUT="$2"; shift 2 ;;
    *) shift ;;
  esac
done
[ -n "$OUT" ] || OUT="tmp/CMP_${MASTER}_ep${EP}.mp4"
mkdir -p "$(dirname "$OUT")" tmp
[ -f tmp/abfont.ttf ] || cp /c/Windows/Fonts/arial.ttf tmp/abfont.ttf 2>/dev/null

# ★ 本机 ffmpeg **没有 fontconfig**：drawtext 不带 fontfile 会段错误（实测 rc=139），
#   所以字体一律拷一份到仓库里、用相对路径显式指过去。
FONT="drawtext=fontfile=tmp/abfont.ttf:"
F1="projects/${MASTER}-reference/media/ep${EP}/episode_final.mp4"
F2="projects/${MASTER}-pack/media/ep${EP}/episode_final.mp4"
F3="projects/${MASTER}-mixed/media/ep${EP}/episode_final.mp4"
MISS=0
for f in "$F1" "$F2" "$F3"; do
  [ -f "$f" ] || { echo "⛔ 缺成片：$f"; MISS=1; }
done
[ "$MISS" = 0 ] || exit 2

echo "=== 三臂成片时长与画幅（这些差异本身就是结论）==="
for f in "$F1" "$F2" "$F3"; do
  printf '%-46s ' "$(basename "$(dirname "$(dirname "$(dirname "$f")")")")"
  ffprobe -v error -select_streams v:0 -show_entries stream=width,height \
      -show_entries format=duration -of csv=p=0 "$f" | tr '\n' ' '; echo
done

ffmpeg -v error -y -i "$F1" -i "$F2" -i "$F3" -filter_complex \
  "[0:v]scale=-2:640,${FONT}text='reference':fontsize=32:fontcolor=white:x=14:y=14[a];\
[1:v]scale=-2:640,${FONT}text='pack':fontsize=32:fontcolor=white:x=14:y=14[b];\
[2:v]scale=-2:640,${FONT}text='mixed':fontsize=32:fontcolor=white:x=14:y=14[c];\
[a][b][c]hstack=inputs=3[v]" -map "[v]" -an -c:v libx264 -pix_fmt yuv420p "$OUT" \
  || { echo "⛔ 并排片合成失败"; exit 3; }
echo "✅ 并排片：$OUT（左 reference / 中 pack / 右 mixed；三路静音）"
