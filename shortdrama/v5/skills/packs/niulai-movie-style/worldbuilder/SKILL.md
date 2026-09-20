---
name: worldbuilder
description: 锁定项目简报与牛来低模视觉风格指南（primitive folk CGI：真诚的技术不足审美，故意低质、精致即失败），产出故事大纲与世界观；渲染风格全链路以此为准。
---

> 类型包 niulai-movie-style ｜ 移植自 Kido G 社区包 v1.0.0（MiniMax-hub 导出；模型中立：视频/图像层 Agnes，MiniMax-H3 待接入）。核心审美：**真诚的技术不足**（sincere technical insufficiency）——故意笨拙的原始低模 3D，精致低模 = 失败。

## 管线适配（本工作流专属约定）

- 输出 `worldbuilder/worldbuilder.md`：故事大纲 + 世界观 + **视觉风格指南（渲染风格的唯一权威）**。
- 视觉风格指南必须显式声明：primitive_folk_cgi 预设（极低面数/失真比例/僵硬绑定/局部穿模 1-3 处/粗糙脸部/低清重复贴图/资产复用/朴素单光源/廉价渲染）；下游分镜「视觉风格」段、图像与视频提示词、QC 判定全部以此为准。
- 锚点保留原则（写进风格指南）：保留主体数量/大类、大布局、语义动作、镜头、场景类别、主要道具、3-6 个大色块；主动放弃精确解剖/干净轮廓/微表情/微纹理。
- 源图（用户照片/参考图）是身份锚点来源：物种、发型块、服装类别、标记、人物关系必须可读；不改变显著年龄/族裔/身体类别。

---

# Style system

Use this reference to choose a preset or tune a difficult source. Define the look
through observable production limits rather than one film, game, engine, or meme.

## Visual thesis

Aim for **sincere technical insufficiency**: an understandable scene built with too
few polygons, failed proportions, crude rigging, poor collision, too few textures,
too few assets, and lighting placed without mature cinematography. The charm should
be accidental, direct, and awkward rather than a refined low-poly aesthetic.

## Eleven style pillars

### 1. Coarse geometry

- Use few large planes, awkward edge flow, hard normals, and lumpy silhouettes.
- Build joints from blocks, hands from wedges or rectangular fingers, and faces
  from crude cheek, brow, nose, jaw, and mouth planes.
- Use visibly fewer masses, not merely smaller triangles. Heads, torsos, limbs, and
  animal bodies should not retain smooth source-faithful outlines through dense facets.
- Avoid clean triangular mosaics, elegant facets, smooth subdivision, and toy forms.

### 2. Broken proportions

- Release source-faithful head/body ratio, limb length, torso width, neck length,
  joint size, paw/hoof scale, muzzle size, and animal body mass.
- Mix oversized heads or paws with short limbs, rigid necks, broad torsos, narrow
  shoulders, uneven leg lengths, or mismatched primitive parts.
- Keep species, person count, clothing category, semantic action, and broad identity
  readable. The result may be foolish or clumsy, but not horrific or injury-like.

### 3. Failed pose and rigging

- Preserve the meaning of the action while changing exact joint angles and balance.
- Use locked torsos, shoulders lifted too high, elbows kinked on one axis, wrists
  that do not compensate, straight knees, planted feet, floating hands, and weak contact.
- Remove natural counter-pose, subtle weight transfer, muscle deformation, and the
  smooth shoulder/elbow/hip behavior of a competent character rig.

### 4. Visible collision failures

- Add one to three plausible intersections in contact-heavy images: sleeve into
  elbow, upper arm through shoulder or garment, hand into sleeve or held prop,
  loose clothing into torso, fur into harness, or adjacent bodies slightly intersecting.
- Keep clipping legible and local. Do not hide faces, erase full limbs, expose bodies,
  imply injury, or make every joint penetrate every mesh.

### 5. Unrefined faces and gaze

- Reuse simple eyeball and eye-texture construction across multiple characters.
- Allow unequal eye sizes, slightly off-center pupils, imperfect gaze convergence,
  stiff eyelids, crude brows, flat mouth slits, and frozen expressions.
- Keep the broad emotion readable; remove professional subtle acting and beauty polish.

### 6. Tiny rough textures

- Use visibly small, blurry diffuse maps with seams, mirrored details, UV stretching,
  inconsistent scale, crude painted shadows, and limited color steps.
- Make diffuse/base color the dominant or only material channel. Remove coherent
  PBR response, normal/displacement detail, subsurface skin, layered fabric/fur,
  clearcoat, and realistic reflections. Mix dead-flat areas with a few wrongly shiny patches.
- Replace hair strands, fur grooming, pores, fabric weave, and fine embroidery with
  noisy repeated patches or a few thick texture cards.

### 7. Obvious repetition

- Reuse the same bark, grass, plank, stone, wall, fur, scale, cloth, skin, hair,
  and eye maps where plausible.
- Make tiling visible. Repetition is evidence of a small asset budget, not a flaw
  to hide with procedural variation.

### 8. Sparse repeated background

- Keep only the minimum scene masses required to identify the setting.
- Reuse two or three tree, bush, post, rock, building, beam, or prop models many times.
- Remove small signs, decorative trim, incidental set dressing, and unique clutter.
- Avoid both detailed environments and deliberately clean voxel worlds.

### 9. Naive lighting

- Prefer one blunt directional or overhead-front light plus weak ambient fill.
- Allow flat local color, clipped foreheads/noses/hands, muddy eye sockets and necks,
  hard low-resolution shadows, weak contact shadows, and exposure mismatch.
- Avoid rim lights, beauty lights, three-point setups, soft bounce, global illumination,
  volumetric beams, bloom, cinematic fog, and carefully shaped facial light.

### 10. Cheap rendering and capture

- Use weak antialiasing, limited texture filtering, basic shadow maps, jagged edges,
  mild pixel crawl, fine noise, slight color fringe, and screen-capture softness.
- Keep these effects secondary. Do not substitute VHS, CRT, glitch, mosaic, or
  heavy JPEG damage for actual primitive assets.

### 11. Plain mood and color

- Anchor three to six large colors in the source, then render them bluntly.
- Favor sincere, homemade, literal, slightly washed-out or overexposed color.
- Keep dark scenes readable. Do not default to horror, sepia, teal-orange cinema,
  dramatic desaturation, or premium atmospheric depth.

## Presets

### `primitive_folk_cgi` — default

For general images, animals, people, landscapes, objects, and mixed scenes.

- extreme reconstruction; medium identity lock; extremely-low polygon budget;
- broken human/animal proportions, failed rigid posing, and local visible clipping;
- crude asymmetrical meshes and stiff gaze;
- obvious texture tiling and heavy asset reuse;
- sparse repetitive background;
- one naive light plus flat ambient;
- low production value that remains readable.

### `bright_folk_cgi`

Use when the user wants more likeness or a gentler result.

- high reconstruction; high identity lock;
- readable angular faces with fewer intentional gaze errors;
- rough textures and repeated background assets, but less aggressively;
- bright simple daylight with awkward exposure.

### `sunlit_game_map`

For architecture, streets, travel photos, and spatial scenes.

- modular block geometry; repeated facade and ground maps;
- very small prop library and sparse set dressing;
- blunt sky light plus one direction light; simple baked-looking shadows;
- no automatic dark industrial mood.

### `community_cgi_stage`

For portraits, dialogue, group shots, performances, and frontal compositions.

- exact head count and spacing; medium identity lock; semantic-action pose lock only;
- shared eye, skin, cloth, and hair assets; stiff hands and frozen expressions;
- local arm/sleeve/shoulder intersections where figures touch or gesture;
- shallow staged depth and repeated beams/backdrops;
- plain overhead-front light, uneven faces, and no cinematic separation.

### `rough_night_render`

Use only for genuine night sources or explicit night requests.

- a few crude point lights, short falloff, hard shadow maps, and dark gaps;
- repeated emissive maps; readable local colors;
- no horror treatment unless requested.

## Source adjustments

| Source | Lock | Deliberately reduce | Reuse |
|---|---|---|---|
| Portrait | count, facing direction, semantic action, hair mass, clothing blocks | silhouette, head/body ratio, exact pose, face planes, eye alignment, garment collision | eye/skin/hair maps |
| Group | count, spacing, semantic gestures, color blocks | individual proportions, exact joint angles, contact, fingers, garment fit | eyes, skin, cloth, hair assets |
| Animal | species, markings, semantic action, muzzle direction | body ratio, leg/paw scale, balance, fur collision, joints, facial refinement | fur/scale/eye maps |
| Landscape | horizon, landform, palette | foliage variety, rock detail, atmosphere | trees, bushes, grass, rocks |
| Architecture | massing, openings, perspective | trim, glass, facade detail, clutter | wall, roof, ground modules |
| Food/object | silhouette, count, arrangement | curves, labels, microtexture, reflections | surface and label-free maps |

## Strength and fidelity

- `medium`: recognizable and restrained; use only when likeness dominates.
- `high`: every object reads as rebuilt low-poly; detail remains moderately faithful.
- `extreme` (default): preserve subject semantics and composition only; release exact
  pose, silhouette, anatomy, and collision. Use extremely sparse primitive geometry,
  broken proportions, stiff failed rigging, local clipping, repeated maps, sparse
  assets, stiff gazes, and naive lighting.

Use `identity_lock: medium` by default. Raise to `high` only for explicit likeness;
never lower broad composition, semantic action, and subject-count/type locks.
