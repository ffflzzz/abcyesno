---
name: director
description: 把剧本改编为镜头表并应用牛来运镜规则（锁定机位跳切、线性平移、突兀启停、僵硬主角运动、整身轴向转动）；7 列结构不变，运镜与每秒指令体现技术不足审美。
---

> 类型包 niulai-movie-style ｜ 移植自 Kido G 社区包 v1.0.0（MiniMax-hub 导出；模型中立：视频/图像层 Agnes，MiniMax-H3 待接入）。核心审美：**真诚的技术不足**（sincere technical insufficiency）——故意笨拙的原始低模 3D，精致低模 = 失败。

## 管线适配（本工作流专属约定）

- 输出 `director/director.md`：7 列镜头表（Shot ID & Duration | Continuity Handoff | Reference Anchors | Hook Type | Shot Description(每秒指令) | Audio & Dialogue Track | Audio Mode）——**列结构不可变**（渲染层解析依赖）。
- 每秒指令的运镜/动作维度按下方牛来规则执行（僵硬分段运动、整身转动、匀速直线、姿势弹出、滑步、空洞眼神；锁定机位跳切、线性运镜、突兀启停、穿模机位、尴尬低角近景）。
- 时长 4-12s 按剧情节拍灵活分配，避免全表等长。
- **反文字模拟（硬性）**：镜头描述与每秒指令中禁止「模拟文字/标题/logo」类要求——会渲染出真实可读文字（P0）；招牌/标题一律为纯色粗糙几何块。
- 每镜时长与 Mouth State 规则照常：对白秒单说话人，非对白秒闭嘴。

---

# Niulai protagonist motion and camera rules

Use this reference when the user asks for Niulai-style motion, video prompts, camera movement, 主角运动, 运镜, or when an image result will later be animated. These rules come from a reference video analysis and describe reusable production behavior, not a specific shot to copy.

## Motion thesis

Niulai motion should feel like a technically limited early-3D character system: the protagonist is readable, but movement is rigid, segmented, and physically under-simulated. Avoid polished animation curves, natural anticipation, realistic weight transfer, and cinematic camera smoothing.

## Protagonist motion rules

- **Stiff idle posture:** when still, keep the body upright or linearly posed, with limbs held away from the body and no convincing weight distribution.
- **Whole-body pivot turns:** rotate head, neck, torso, and hips as one crude rigid unit around a vertical axis. Avoid layered neck/shoulder/hip compensation.
- **Linear translation:** jumps, falls, dodges, or forward movement travel along straight paths at nearly constant speed, without natural acceleration or deceleration.
- **No anticipation or impact absorption:** remove wind-up, squash, compression, knee bend, and soft landing recovery. The body pops into motion and stops abruptly.
- **Pose popping:** shock, pain, realization, or panic is expressed by snapping into a pose, such as both hands touching the head, then holding with tiny mechanical shakes.
- **Sliding feet:** feet may skate across the ground or fail to match the walking cycle. Ground contact can be weak, late, or visibly misregistered.
- **Rigid gestures:** arms swing or lift in one-axis rotations, wrists stay straight, shoulders rise too high, and hands float or intersect nearby meshes.
- **Vacant eye behavior:** gaze stays static, misaligned, or poorly converged during motion; do not add polished eye tracking or expressive facial animation.

## Camera and framing rules

- **Static-camera jump cuts:** prefer cuts between locked-off cameras. The character moves awkwardly inside a fixed frame rather than being followed smoothly.
- **Linear camera moves:** when the camera pans, dollies, or zooms, use simple linear motion with abrupt starts and stops. Avoid ease-in, ease-out, handheld sophistication, or stabilized cinematic arcs.
- **Collision-disregarding camera:** the camera path may clip through foliage, rocks, props, or background geometry instead of elegantly avoiding obstacles.
- **Awkward low-angle close-ups:** use slightly low or flat-perspective close-ups that emphasize crude face planes and primitive geometry.
- **Sudden scale offsets:** dramatic moments can cut abruptly to extreme close-ups of a body part, object, or face without transitional coverage.
- **Unmotivated reframing:** allow small framing mismatches, hard cuts, and blunt position jumps, as if assembled in a simple game engine cutscene tool.

## Timing and beat behavior

- Build scenes from short readable action beats rather than fluid continuous acting.
- Let major changes happen as pops, cuts, or constant-speed translations.
- Hold awkward poses slightly too long after a gesture, landing, or realization.
- Use tiny mechanical shakes sparingly; the main failure should remain rigging and camera simplicity, not glitch effects.

## Prompt clause

Use or adapt this clause in video/image-to-video prompts:

```text
Motion and camera should feel like a primitive early-3D cutscene: stiff idle posture, whole-body pivot turns, one-axis arm gestures, sliding feet, constant-speed jumps or translations, no anticipation, no soft landing, pose-popping reactions, vacant misaligned gaze, locked-off static-camera jump cuts, linear pans or zooms with abrupt starts and stops, occasional camera clipping through simple background geometry, and awkward low-angle close-ups. Avoid polished animation curves, natural weight transfer, expressive facial acting, smooth handheld cinematography, and cinematic easing.
```
