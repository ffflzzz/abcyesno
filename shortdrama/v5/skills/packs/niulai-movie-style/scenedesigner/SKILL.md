---
name: scenedesigner
description: 把镜头表展开为文本分镜：画面描述按牛来提示词蓝图组织（可观察的生产失败而非空泛标签，锚点锁定+主动放弃），11 字段结构与每秒五象限面板不变。
---

> 类型包 niulai-movie-style ｜ 移植自 Kido G 社区包 v1.0.0（MiniMax-hub 导出；模型中立：视频/图像层 Agnes，MiniMax-H3 待接入）。核心审美：**真诚的技术不足**（sincere technical insufficiency）——故意笨拙的原始低模 3D，精致低模 = 失败。

## 管线适配（本工作流专属约定）

- 输出到 **system prompt 的【本角色产物路径】给出的那个文件**：**一张 markdown 表格**（表头行 + 每镜一行的数据行），列结构：
  `镜头号 | 景别 | 角度 | 运镜 | 时长(秒) | 场景 | 画面描述 | 对白 | 音效`
  ——**列结构与顺序不可变**（渲染层**按列名**解析；**缺列会被静默按空串处理**，日志里看不出来）。
  ⚠️ **不要**写成 `## S<N> / <时长>s` 分节的块状结构 —— 那是旧版契约的歧义表述，渲染层只读表格。
- **`场景`（必填，2026-09-14 新增）**：本镜所在的**场景资产名**，与 `assetdesigner/assets.md` 登记的场景名**逐字一致**，写成 `@<场景名>`。
  - 管线取该资产的描述（**光源 / 色温 / 陈设**）作**场景锚点**注入本镜提示词 —— 这是**全片光源的唯一出处**，
    也是「同一场戏切镜不换场景」的**唯一保障**。
  - **同一场戏的每一镜必须写同一个场景名**。漏写或写错名 → 该镜没有场景锚点 → 背景与光线由模型自由发挥 → **切镜就换场景**（实测事故）。
  - **光源 / 色温只写在场景资产的描述里**，画面描述与画面外不要另写与之矛盾的光。
- **列「值」规范（硬性，违反会被分镜契约门拦下，整条媒体链起不来）**：
  上一条只规定"列结构与顺序"，**值怎么写同样是渲染层依赖**。实测（2026-09-14）因为没写清，
  同一个模型写出过**四种**镜头号写法（`S01` / 漏行首 `|` / `镜1` / 重号），每次都要人工排查：
  - **`镜头号`：全片唯一、从 1 连续递增**。只写数字（`1`、`2`…）或 `S01`/`LN01`；**不得重号**，
    也**不得**让同一镜占两行（要分两个镜就写**两个不同的镜头号**）。
  - **`时长(秒)`：必须是阿拉伯数字**（如 `7`），**不得写 `—`、不得留空**；单镜 4–12 秒。
  - **一镜一行**：每行是一支可独立渲染的镜；镜数 = 行数。
  - 各行「时长」之和 = 全片总时长，必须落在 brief 目标时长的 **85%–130%**（分镜契约门按此校验）。
  - 表头行与数据行都要以 `|` 开头（markdown 表格合法性）。
- 画面描述按下方提示词蓝图组织：声明原始低成本三维重建 → 锁定语义/构图锚点 → 极低面数与破坏比例 → 僵硬绑定与穿模位置 → 粗糙五官 → 低清重复贴图与资产复用 → 背景简化为重复场景块 → 朴素单光源与廉价渲染 → 比例与禁止项。**写可观察的生产失败，不用「丑/低质」空泛标签。**
- 每秒五象限面板（Timecode | Pose+Expression | Camera | Audio+Anchor | Mouth State）照常；对白秒单说话人。
- 资产绑定：关键道具/资产逐字使用 assets.md 登记名。
- **反文字模拟（硬性）**：分镜中禁止任何「模拟文字/发光文字/标题形状/logo」类要求——要求模型模拟文字必然渲染出真实可读文字（P0）。招牌、标题、门牌一律写「纯色粗糙几何块/发光色块（无任何字符形状）」；叙事需要文字信息时改用动作或道具表达。

---

# Prompt blueprint and parameters

Use this reference to convert source analysis and user choices into one executable
image-edit prompt. Replace variables; do not paste the schema into a generation tool.

## Parameter schema

```yaml
skill: niu-lai-translator
preset: primitive_folk_cgi | bright_folk_cgi | sunlit_game_map | community_cgi_stage | rough_night_render
reconstruction_strength: medium | high | extreme
anchor_lock: strict | high
identity_lock: medium | high
detail_budget: very_low | low
geometry: primitive_low_poly | visibly_low_poly
polygon_budget: extremely_low | very_low
face_geometry: clumsy_asymmetric | strongly_faceted | angular_readable
gaze_quality: stiff_misaligned | crude_readable | source_faithful
proportion_fidelity: deliberately_broken | loosely_source_based
pose_lock: semantic_action_only | broad_pose
pose_quality: stiff_failed_rig | crude_rigid | source_faithful
collision_quality: visible_clipping | imperfect_contact | clean
clipping_count: 1_to_3 | sparse | none
texture_resolution: very_low | low
material_model: diffuse_only_mismatched | flat_unlit | crude_lambert
texture_repetition: obvious | strong | moderate
asset_reuse: heavy | strong | moderate
background_detail: sparse | reduced
lighting: naive_single_light | flat_stage_light | hard_awkward_daylight | crude_point_lights
palette: source_anchored_blunt | source_anchored_saturated
post_effects: none | subtle_capture_noise | mild_color_fringe
text_mode: none | preserve_exact | user_text
ratio: source_ratio | 1:1 | 3:4 | 4:3 | 9:16 | 16:9
```

## Production prompt template

```text
Rebuild the supplied image as a primitive, visibly low-budget 3D scene. This is
not a blur filter, compression pass, or fashionable low-poly illustration.

HARD ANCHOR LOCK — Preserve [subject count/types], [left-to-right arrangement],
[semantic action or relationship], [crop/camera/perspective], [depth layers], [scene
category], [major props], and [three to six dominant color blocks]. Preserve what
the subjects are doing, not their exact joint angles, anatomical silhouette, body
ratios, or clean mesh separation. Recognition should come from meaning and layout.

DETAIL BUDGET — Use [preset], [reconstruction_strength], and [detail_budget].
Remove subtle facial acting, hair strands, fingers, embroidery, microtexture,
decorative architecture, small signs, and incidental set dressing.

TOPOLOGY — Rebuild forms with [geometry] and [polygon_budget]. Use a handful of
large awkward planes and primitive masses, hard normals, block joints, wedge hands,
crude paws/hooves, and thick hair clumps/cards. Do not preserve smooth outlines by
covering them with many small facets. No silhouette-smoothing support loops.

PROPORTION FAILURE — Use [proportion_fidelity]. Deliberately depart from source
proportions while preserving category and identity anchors: mismatch limb lengths,
head/body ratio, neck length, torso width, joint size, muzzle size, paw/hoof scale,
and animal body mass. Prefer awkward assembled parts over elegant caricature.

POSE AND RIGGING FAILURE — Use [pose_lock] and [pose_quality]. Preserve the semantic
action but alter exact pose. Lock the torso, rotate joints on one crude axis, lift
shoulders too high, kink elbows, keep wrists straight, plant feet, weaken balance,
float hands, and remove natural counter-pose and weight transfer.

COLLISION FAILURE — Use [collision_quality] with [clipping_count] readable local
intersections chosen from actual contact zones in this source: [source-tailored
clipping locations]. Allow sleeve/elbow, upper-arm/shoulder, arm/garment, hand/sleeve,
hand/held-prop, fur/harness, or adjacent-body penetration. Do not hide faces, erase
whole limbs, expose bodies, imply injury, or clip every contact.

FACES AND GAZE — For faces use [face_geometry] and [gaze_quality]: simple
reused eye construction, unequal eye sizes, slightly off-center pupils, imperfect
gaze convergence, stiff lids, block noses, flat mouth slits, and frozen expressions.
Keep broad intent readable without professional animation polish.

MATERIALS, TEXTURES, AND REUSE — Use [material_model] with diffuse/base color as the
dominant or only channel. Remove coherent PBR, normal/displacement, subsurface,
clearcoat, layered fabric/fur, and realistic reflection models. Mix flat matte areas
with a few wrongly shiny patches and inconsistent response between nearby objects.
Use [texture_resolution] maps with [texture_repetition] tiling, seams, UV stretching,
mirrored details, inconsistent scale, noisy pixels, and crude painted shadows. Use [asset_reuse]: visibly reuse
a tiny library of skin, eye, hair, cloth, fur, scale, plank, bark, grass, stone,
wall, and ground assets as relevant.

BACKGROUND — Set [background_detail]. Keep only essential setting masses. Reuse
two or three primitive background models repeatedly with minimal variation; remove
unique clutter and decorative detail.

LIGHT AND RENDER — Use [lighting]: one blunt directional or overhead-front light
plus weak flat ambient fill. Allow clipped highlights, muddy dark patches, hard
low-resolution shadows, weak contact, and inconsistent nearby exposure. Use weak
antialiasing, limited filtering, basic shadow maps, [post_effects] kept secondary,
and no professional scene separation.

OUTPUT — [ratio]. [text instruction].

DO NOT — [source-tailored prohibitions], exact premium reproduction, correct
source-faithful proportions, natural weight transfer, clean collision everywhere,
attractive aligned eyes, refined facial acting, smooth subdivision, dense small
facets masquerading as low-poly, clean designer facets,
unique high-resolution materials, rich detailed background, rim light, beauty
light, three-point lighting, volumetric beams, cinematic depth, PBR, glossy toys,
photorealism, modern game polish, darkness as a shortcut, VHS/CRT/glitch/mosaic,
random text, UI, characters, props, logos, or cow traits absent from the source.
```

## Compact prompt

Use only when the editor follows source images reliably:

```text
Rebuild the supplied image as exaggeratedly failed primitive folk CGI. Lock subject
count/type, broad arrangement, semantic action, camera, scene category, props, and
color blocks; release exact pose, silhouette, anatomy, and mesh separation. Use an
extremely low polygon budget made of a handful of awkward masses, not many small
facets. Deliberately distort human/animal proportions. Make the torso rigid, joints
single-axis, shoulders too high, elbows kinked, wrists straight, balance poor, and
contact weak. Add one to three local visible intersections at source-evidenced contact
zones such as sleeve/elbow, arm/shoulder, hand/prop, garment/torso, or fur/harness;
keep faces and whole limbs visible. Use stiff unequal eyes with slightly misaligned
pupils, crude face wedges, thick hair cards, wedge hands, diffuse-only materials,
dead-flat surfaces mixed with a few wrongly shiny patches, tiny blurry maps, obvious
repeated tiling, and a tiny background asset library copied many times.
Light everything with one blunt overhead-front light plus weak flat ambient: clipped
faces, muddy eye sockets, hard cheap shadows, uneven exposure, no rim or cinematic
separation. Weak antialiasing and filtering only. No correct source-faithful anatomy,
natural rigging, clean collision everywhere, polished low-poly art, detailed sets,
unique clean textures, professional facial acting, PBR, beauty light, horror, VHS,
text, UI, new subjects, props, logos, cows, or horns.
```

## Negative constraint block

```text
Avoid: fine likeness; source-faithful proportions; natural counter-pose; correct
weight transfer; flexible realistic joints; perfectly separated garments and limbs;
attractive aligned eyes; expressive professional facial animation; correct smooth
anatomy; dense small facets; clean triangular mosaics; rounded toy forms;
coherent PBR materials; normal/displacement maps; subsurface skin; realistic fabric,
fur, reflections, or layered roughness; unique high-resolution textures; hidden
tiling; procedural background variety;
rich set dressing; cinematic lighting; three-point light; rim light; soft bounce;
global illumination; volumetric fog; depth of field; premium game graphics;
photorealism; darkness or horror as a shortcut; heavy blur; large pixelation;
JPEG damage; VHS; CRT; glitch; invented text, UI, logos, characters, or props.
```

## Text handling

- `none` (default): omit incidental signs, subtitles, watermarks, and interface text.
- `preserve_exact`: quote exact source text and inspect character by character.
- `user_text`: include only exact user-supplied text; invent nothing.

## Source recipes

### Portrait or group

Use `community_cgi_stage` for groups and `primitive_folk_cgi` for a single figure.
Lock count, spacing, semantic action, hair mass, garment categories, and color blocks.
Reuse eyes, skin, hair, and cloth maps. Distort ratios, lock the torso, kink limb
joints, and add local sleeve/arm/shoulder clipping when contact exists.

### Animal scene

Use `primitive_folk_cgi`. Lock species, count, semantic action, markings, and composition.
Change body ratios, leg length, paw/hoof scale, neck and muzzle proportions, and
balance. Use rigid joints and local fur/harness or limb/body clipping where plausible.
Reuse fur/scale/eye maps and reduce facial refinement and background variety.

### Architecture or landscape

Use `sunlit_game_map`. Lock perspective, horizon, landform, massing, and openings.
Reuse a tiny module library and visibly tile wall, ground, grass, tree, and rock maps.

### Lighting-only follow-up

Lock all geometry, materials, poses, and composition. Replace only lighting with
`naive_single_light`: blunt overhead-front direction, weak ambient, clipped faces,
muddy creases, hard low-resolution shadows, weak contact, and uneven exposure.
