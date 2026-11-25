# 三套服装的完整资产清单

[English](asset-checklist.en.md) · [项目首页](../README.zh-CN.md) · [安装与使用](quickstart.zh-CN.md)

在 **ZZZ Toon → 角色** 里选名字，再对照该角色的清单。**第一次生成预览，先备齐“基础文件”。要和项目的完整参考工程比较，再核对“参考工程的辅助文件”。** 每行对应一个输入框。文件名是验证时的名称，改过名也能手动选，但文件内容、服装和动作必须匹配。

想边找边打勾，可以把本页复制到自己的 Markdown 笔记里。不同角色可能都有名为 `uv.fbx` 或 `lut.exr` 的文件，请从对应服装的目录选取。

| 点角色跳到清单 | 模型和待机 | 必填贴图 | 完整参考另填 | 合计 |
| --- | ---: | ---: | ---: | ---: |
| [Remielle](#remielle) | 2 | 24 | 27 | 53 |
| [Velina](#velina) | 2 | 24 | 31 | 57 |
| [Vivian](#vivian) | 2 | 23 | 18 | 43 |

我们在 Blender 5.3.0 重新打开了每个角色的两份 v0.52.1 工程，逐栏核对文件。三份基础工程都能播放待机。三份完整工程使用下方列出的全部辅助文件，生成记录里的替代项和缺失项均为空。这个清单能复现项目验证时的输入。游戏录像中的转身和物理摆动仍有差异。

## 先看懂这几种文件

- **插件已包含**三套服装的材质设置、预览灯光与相机设置、已验证待机的骨骼修正。选择具名角色会自动载入。用户提供自己的模型、动画、贴图和需要的辅助数据。具名入口无需填写原始材质表或整个 FrameAnalysis 抓帧目录。
- **可以先留空**的有采样图集（Mip）、调色表（LUT）和背景图层。插件会从主贴图生成远看时使用的小尺寸图，用内置近似调色，并生成滚动背景。对照原参考时，使用清单里的对应文件。想换自定义背景，可以在“整张滚动背景”里选一张可平铺图片，它会替代三层背景。
- **进阶辅助文件**包括带额外 UV 的 FBX、原始网格 JSON、顶点数据 NPZ。UV 决定图案落在模型的哪里。NPZ 是为这套网格整理好的顶点、眼睛或描边数据，需要匹配顶点顺序和 UV。有些数据能从已验证的原始 FBX 读取，各角色下方列出了基础工程实测能恢复的部分。GitHub 提供插件和预设，游戏文件及项目专用的参考数据需自行准备。

贴图后缀通常表示用途：**D** 是底色，**N** 用于法线和光照，**M** 是材质遮罩，**A** 是辅助通道。**MatCap** 用于表现材质的反光。各通道的用法可看[着色图文教程](shading.zh-CN.md)。

## Remielle

羽翼的底色和辅助通道叫 `Remielle_Wings_…`，法线和遮罩则叫 `Remielle_Ramiel_Wings_…`。基础工程从原 FBX 恢复了身体第 2 组的部分法线、描边坐标、背面 UV 和头发投影的顶点红通道。优先核对羽翼、头发和身体第 1 组的网格，以及脸和眼睛的数据。

### 基础文件（26 项）

**模型与待机**

- [ ] `Avatar_Female_Size02_RemielleOrigin_UI.fbx` — 模型 FBX
- [ ] `Avatar_Female_Size02_Remielle_Origin_Ani_Idle_Loop.anim` — 待机动画

**脸部与眼睛**

- [ ] `Eye_E.png` — 眼睛颜色查找表
- [ ] `Female_Face_Lightmap_02.png` — 面部光照图
- [ ] `Remielle_Face_D.png` — 脸部 · 底色

**头发**

- [ ] `Remielle_Hair_A.png` — 头发 · 辅助通道
- [ ] `Remielle_Hair_D.png` — 头发 · 底色
- [ ] `Remielle_Hair_M.png` — 头发 · 材质遮罩
- [ ] `Remielle_Hair_N.png` — 头发 · 法线与光照

**服装**

- [ ] `Remielle_Origin_Body_1_A.png` — 服装 1 · 辅助通道
- [ ] `Remielle_Origin_Body_1_D.png` — 服装 1 · 底色
- [ ] `Remielle_Origin_Body_1_M.png` — 服装 1 · 材质遮罩
- [ ] `Remielle_Origin_Body_1_N.png` — 服装 1 · 法线与光照
- [ ] `Remielle_Origin_Body_2_A.png` — 服装 2 · 辅助通道
- [ ] `Remielle_Origin_Body_2_D.png` — 服装 2 · 底色
- [ ] `Remielle_Origin_Body_2_M.png` — 服装 2 · 材质遮罩
- [ ] `Remielle_Origin_Body_2_N.png` — 服装 2 · 法线与光照

**武器与羽翼**

- [ ] `Remielle_Ramiel_Wings_M.png` — 羽翼 · 材质遮罩
- [ ] `Remielle_Ramiel_Wings_N.png` — 羽翼 · 法线与光照
- [ ] `Remielle_Wings_A.png` — 羽翼 · 辅助通道
- [ ] `Remielle_Wings_D.png` — 羽翼 · 底色

**MatCap**

- [ ] `Eff_MatCap_019.png` — MatCap 019 · 服装 2
- [ ] `Eff_MatCap_077.png` — MatCap 077 · 服装 2
- [ ] `Eff_Matcap_092.png` — MatCap 092 · 服装 2
- [ ] `Eff_Matcap_112.png` — MatCap 112 · 服装 2
- [ ] `Eff_Matcap_115.png` — MatCap 115 · 服装 1

### 参考工程的辅助文件（27 项）

**几何辅助数据**

- [ ] `Remielle_Wings.json` — Remielle_Wings · 原始网格
- [ ] `Remielle_Hair.json` — Remielle_Hair · 原始网格
- [ ] `Remielle_Origin_Body_1.json` — Remielle_Origin_Body_1 · 原始网格
- [ ] `Remielle_Origin_Body_2.json` — Remielle_Origin_Body_2 · 描边网格
- [ ] `Remielle_Origin_Body_1_native_inputs.npz` — Remielle_Origin_Body_1 · 表面顶点数据
- [ ] `Remielle_Origin_Body_2_outline_inputs.npz` — Remielle_Origin_Body_2 · 表面顶点数据
- [ ] `Remielle_Wings_native_inputs.npz` — Remielle_Wings · 表面顶点数据
- [ ] `Remielle_face_inputs.npz` — Remielle_Face · 脸部顶点数据
- [ ] `original_eye_inputs.npz` — Remielle_Face · 眼部顶点数据
- [ ] `Remielle_caster.npz` — 面部投影遮挡数据
- [ ] `face_outline.npz` — 脸部轮廓顶点数据

**颜色与细纹**

- [ ] `Remielle_character_lut.exr` — 角色调色 LUT

**采样图集**

- [ ] `Remielle_Origin_Body_2_D_mips_f32.exr` — 服装 2 · 底色
- [ ] `Remielle_Origin_Body_2_N_mips_f32.exr` — 服装 2 · 法线与光照
- [ ] `Remielle_Origin_Body_2_M_mips_f32.exr` — 服装 2 · 材质遮罩
- [ ] `Remielle_Origin_Body_2_A_mips_f32.exr` — 服装 2 · 辅助通道
- [ ] `Remielle_Origin_Body_1_D_mips_f32.exr` — 服装 1 · 底色
- [ ] `Remielle_Origin_Body_1_N_mips_f32.exr` — 服装 1 · 法线与光照
- [ ] `Remielle_Origin_Body_1_M_mips_f32.exr` — 服装 1 · 材质遮罩
- [ ] `Remielle_Origin_Body_1_A_mips_f32.exr` — 服装 1 · 辅助通道
- [ ] `Remielle_Wings_D_mips.exr` — 羽翼 · 底色
- [ ] `Remielle_Ramiel_Wings_N_mips.exr` — 羽翼 · 法线与光照
- [ ] `Remielle_Ramiel_Wings_M_mips.exr` — 羽翼 · 材质遮罩
- [ ] `Remielle_Wings_A_mips.exr` — 羽翼 · 辅助通道

**滚动背景**

- [ ] `large.png` — 大字图案
- [ ] `small.png` — 小字图案
- [ ] `screen_shade.png` — 背景明暗底图

## Velina

眼睛使用 `Eye_E_02.png`。基础工程可恢复身体第 2 组和两组武器的切线基，以及头发投影的顶点红通道。脸部阴影或刘海遮挡有偏差时，先查本角色的辅助 UV、脸部顶点和眼部文件。

### 基础文件（26 项）

**模型与待机**

- [ ] `Avatar_Female_Size03_Velina_UI.fbx` — 模型 FBX
- [ ] `Avatar_Female_Size03_Velina_Ani_UI_CharSel_Loop.anim` — 待机动画

**脸部与眼睛**

- [ ] `Eye_E_02.png` — 眼睛颜色查找表
- [ ] `Female_Face_Lightmap_02.png` — 面部光照图
- [ ] `Velina_Face_D.png` — 脸部 · 底色

**头发**

- [ ] `Velina_Hair_Map_A.png` — 头发 · 辅助通道
- [ ] `Velina_Hair_Map_D.png` — 头发 · 底色
- [ ] `Velina_Hair_Map_M.png` — 头发 · 材质遮罩
- [ ] `Velina_Hair_Map_N.png` — 头发 · 法线与光照

**服装**

- [ ] `Velina_Body_Map1_A.png` — 服装 1 · 辅助通道
- [ ] `Velina_Body_Map1_D.png` — 服装 1 · 底色
- [ ] `Velina_Body_Map1_M.png` — 服装 1 · 材质遮罩
- [ ] `Velina_Body_Map1_N.png` — 服装 1 · 法线与光照
- [ ] `Velina_Body_Map2_A.png` — 服装 2 · 辅助通道
- [ ] `Velina_Body_Map2_D.png` — 服装 2 · 底色
- [ ] `Velina_Body_Map2_M.png` — 服装 2 · 材质遮罩
- [ ] `Velina_Body_Map2_N.png` — 服装 2 · 法线与光照

**武器与羽翼**

- [ ] `Velina_Weapon_Map_A.png` — 武器 · 辅助通道
- [ ] `Velina_Weapon_Map_D.png` — 武器 · 底色
- [ ] `Velina_Weapon_Map_M.png` — 武器 · 材质遮罩
- [ ] `Velina_Weapon_Map_N.png` — 武器 · 法线与光照

**MatCap**

- [ ] `Eff_MatCap_006.png` — MatCap 006 · 武器
- [ ] `Eff_MatCap_021.png` — MatCap 021 · 服装 1、服装 2、头发、武器
- [ ] `Eff_MatCap_073.png` — MatCap 073 · 服装 1
- [ ] `Eff_MatCap_121.png` — MatCap 121 · 服装 1、服装 2
- [ ] `Eff_Matcap_Socks_03.png` — MatCap Socks_03 · 服装 2

### 参考工程的辅助文件（31 项）

**辅助 UV**

- [ ] `uv.fbx` — 辅助 UV 模型 FBX

**几何辅助数据**

- [ ] `Velina_Body1.json` — Velina_Body1 · 描边网格
- [ ] `Velina_Body2.json` — Velina_Body2 · 描边网格
- [ ] `Velina_Hair1.json` — Velina_Hair1 · 描边网格
- [ ] `Velina_Hair2.json` — Velina_Hair2 · 描边网格
- [ ] `Velina_Weapon1.json` — Velina_Weapon1 · 描边网格
- [ ] `Velina_Weapon2.json` — Velina_Weapon2 · 描边网格
- [ ] `Velina_Body1_outline_inputs.npz` — Velina_Body1 · 表面顶点数据
- [ ] `Velina_Body2_outline_inputs.npz` — Velina_Body2 · 表面顶点数据
- [ ] `Velina_Weapon2_outline_inputs.npz` — Velina_Weapon2 · 表面顶点数据
- [ ] `Velina_Weapon1_outline_inputs.npz` — Velina_Weapon1 · 表面顶点数据
- [ ] `Velina_face_inputs.npz` — Velina_Face · 脸部顶点数据
- [ ] `Velina_eye_inputs.npz` — Velina_Face · 眼部顶点数据
- [ ] `Velina_caster.npz` — 面部投影遮挡数据
- [ ] `face_outline.npz` — 脸部轮廓顶点数据

**颜色与细纹**

- [ ] `Velina_character_lut.exr` — 角色调色 LUT

**采样图集**

- [ ] `Velina_Weapon_Map_D_mips_f32.exr` — 武器 · 底色
- [ ] `Velina_Weapon_Map_N_mips_f32.exr` — 武器 · 法线与光照
- [ ] `Velina_Weapon_Map_M_mips_f32.exr` — 武器 · 材质遮罩
- [ ] `Velina_Weapon_Map_A_mips_f32.exr` — 武器 · 辅助通道
- [ ] `Velina_Body_Map2_D_mips_f32.exr` — 服装 2 · 底色
- [ ] `Velina_Body_Map2_N_mips_f32.exr` — 服装 2 · 法线与光照
- [ ] `Velina_Body_Map2_M_mips_f32.exr` — 服装 2 · 材质遮罩
- [ ] `Velina_Body_Map2_A_mips_f32.exr` — 服装 2 · 辅助通道
- [ ] `Velina_Body_Map1_D_mips_f32.exr` — 服装 1 · 底色
- [ ] `Velina_Body_Map1_N_mips_f32.exr` — 服装 1 · 法线与光照
- [ ] `Velina_Body_Map1_M_mips_f32.exr` — 服装 1 · 材质遮罩
- [ ] `Velina_Body_Map1_A_mips_f32.exr` — 服装 1 · 辅助通道

**滚动背景**

- [ ] `large.png` — 大字图案
- [ ] `small.png` — 小字图案
- [ ] `screen_shade.png` — 背景明暗底图

## Vivian

透明头发和宝石材质都复用身体第 2 组的贴图，因此清单里没有单独的头发贴图槽。武器贴图对应她的伞。基础工程确认身体第 2 组的背面可以沿用主 UV。伞内侧图案出错时，先核对本角色的辅助 UV 和两份武器网格 JSON。细纹则要查 `CharacterOverlayTex.png`。

### 基础文件（25 项）

**模型与待机**

- [ ] `Avatar_Female_Size02_Vivian_UI.fbx` — 模型 FBX
- [ ] `Avatar_Female_Size02_Vivian_Ani_UI_CharacterSelect_Loop_02.anim` — 待机动画

**脸部与眼睛**

- [ ] `Eye_E.png` — 眼睛颜色查找表
- [ ] `Female_Face_Lightmap.png` — 面部光照图
- [ ] `Vivian_Face_D.png` — 脸部 · 底色

**服装**

- [ ] `Vivian_Body_1_A.png` — 服装 1 · 辅助通道
- [ ] `Vivian_Body_1_D.png` — 服装 1 · 底色
- [ ] `Vivian_Body_1_M.png` — 服装 1 · 材质遮罩
- [ ] `Vivian_Body_1_N.png` — 服装 1 · 法线与光照
- [ ] `Vivian_Body_2_A.png` — 服装 2 · 辅助通道
- [ ] `Vivian_Body_2_D.png` — 服装 2 · 底色
- [ ] `Vivian_Body_2_M.png` — 服装 2 · 材质遮罩
- [ ] `Vivian_Body_2_N.png` — 服装 2 · 法线与光照

**武器与羽翼**

- [ ] `Vivian_Weapon_A.png` — 武器 · 辅助通道
- [ ] `Vivian_Weapon_D.png` — 武器 · 底色
- [ ] `Vivian_Weapon_M.png` — 武器 · 材质遮罩
- [ ] `Vivian_Weapon_N.png` — 武器 · 法线与光照

**MatCap**

- [ ] `Eff_MatCap_021.png` — MatCap 021 · 服装 1、服装 2、头发
- [ ] `Eff_MatCap_035.png` — MatCap 035 · 武器
- [ ] `Eff_MatCap_065.png` — MatCap 065 · 服装 1
- [ ] `Eff_MatCap_077.png` — MatCap 077 · 服装 1
- [ ] `Eff_MatCap_Gem.png` — MatCap Gem · 宝石
- [ ] `Eff_Matcap_025.png` — MatCap 025 · 服装 1
- [ ] `Eff_Matcap_Socks.png` — MatCap Socks · 头发
- [ ] `Eff_Matcap_Socks3.png` — MatCap Socks3 · 服装 2

### 参考工程的辅助文件（18 项）

**辅助 UV**

- [ ] `uv.fbx` — 辅助 UV 模型 FBX

**几何辅助数据**

- [ ] `Vivian_Weapon_02.json` — Vivian_Weapon_02 · 描边网格
- [ ] `Vivian_Weapon_01.json` — Vivian_Weapon_01 · 描边网格
- [ ] `Vivian_Body_2.json` — Vivian_Body_2 · 描边网格
- [ ] `Vivian_Body_1.json` — Vivian_Body_1 · 描边网格

**颜色与细纹**

- [ ] `lut.exr` — 角色调色 LUT
- [ ] `CharacterOverlayTex.png` — 表面细纹

**采样图集**

- [ ] `Vivian_Weapon_N_mips.exr` — 武器 · 法线与光照
- [ ] `Vivian_Weapon_M_mips.exr` — 武器 · 材质遮罩
- [ ] `Vivian_Weapon_D_mips.exr` — 武器 · 底色
- [ ] `Vivian_Weapon_A_mips.exr` — 武器 · 辅助通道
- [ ] `Vivian_Body_1_D_mips.exr` — 服装 1 · 底色
- [ ] `Vivian_Body_1_N_mips.exr` — 服装 1 · 法线与光照
- [ ] `Vivian_Body_1_M_mips.exr` — 服装 1 · 材质遮罩
- [ ] `Vivian_Body_1_A_mips.exr` — 服装 1 · 辅助通道

**滚动背景**

- [ ] `large.png` — 大字图案
- [ ] `small.png` — 小字图案
- [ ] `screen_shade.png` — 背景明暗底图
