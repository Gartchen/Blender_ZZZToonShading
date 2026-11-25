# Complete asset checklist for the three outfits

[简体中文](asset-checklist.zh-CN.md) · [Project overview](../README.md) · [Installation and setup](quickstart.en.md)

Choose a name under **ZZZ Toon → Character**. **For your first preview, collect the base files. To match the inputs used for the project comparisons, check the reference extras as well.** Each line corresponds to one input field. The names are from the tested builds. You can select a renamed file manually if its contents match the outfit.

To tick off files as you find them, copy this page into your own Markdown notes. Different outfits can have files named `uv.fbx` or `lut.exr`. Choose the copy that belongs to your outfit.

| Jump to outfit | Model and idle | Required textures | Reference extras | Total |
| --- | ---: | ---: | ---: | ---: |
| [Remielle](#remielle) | 2 | 24 | 27 | 53 |
| [Velina](#velina) | 2 | 24 | 31 | 57 |
| [Vivian](#vivian) | 2 | 23 | 18 | 43 |

We reopened two v0.52.1 projects per character in Blender 5.3.0 and checked each saved file field. All three base projects played their idle. The three full builds used every listed extra and reported no generated substitutes or missing inputs. The checklist reproduces those input sets. Turns and secondary motion can still differ from the game recordings.

## What the files do

- **Included with the add-on.** Selecting a named outfit loads its material settings, preview lights and camera settings, and the bone corrections for its verified idle. Supply your own character files. The named presets do not ask for material tables or an entire FrameAnalysis capture folder.
- **Safe to leave blank on a first build.** The add-on can generate smaller texture levels (mips) for distant views, use an approximate built-in color grade in place of a LUT, and generate the scrolling background. The reference builds used the listed files for these fields. For your own backdrop, pick a tileable image under **Tiled background image**. It replaces all three background layers.
- **Specialized extras.** A secondary-UV FBX supplies extra coordinates for placing patterns. Mesh JSON and project-specific NPZ files supply geometry, eye or outline data and must match the vertex order and UVs. Some data can be recovered from the verified FBX. Each outfit below lists what the base build recovered. The game assets and reference data must be sourced separately from the public add-on.

The texture suffix helps identify its slot. **D** is base color, **N** is normal and lighting data, **M** is a material mask, and **A** holds auxiliary channels. **MatCap** images supply reflections. [Illustrated shading guide](shading.en.md)

## Remielle

Two wing textures start with `Remielle_Wings_`. The wing normal and mask start with `Remielle_Ramiel_Wings_`. The base build recovered the second body group’s tangent basis, outline coordinates and back-face UVs, plus the red vertex channel for the hair shadow caster. Check the wing, hair and first body meshes, then the face and eye inputs.

### Base files (26)

**Model and idle**

- [ ] `Avatar_Female_Size02_RemielleOrigin_UI.fbx` — FBX
- [ ] `Avatar_Female_Size02_Remielle_Origin_Ani_Idle_Loop.anim` — Idle

**Face and eyes**

- [ ] `Eye_E.png` — Eye color lookup
- [ ] `Female_Face_Lightmap_02.png` — Face lightmap
- [ ] `Remielle_Face_D.png` — Face · Base color

**Hair**

- [ ] `Remielle_Hair_A.png` — Hair · Auxiliary channels
- [ ] `Remielle_Hair_D.png` — Hair · Base color
- [ ] `Remielle_Hair_M.png` — Hair · Material mask
- [ ] `Remielle_Hair_N.png` — Hair · Normal and lighting

**Outfit**

- [ ] `Remielle_Origin_Body_1_A.png` — Outfit 1 · Auxiliary channels
- [ ] `Remielle_Origin_Body_1_D.png` — Outfit 1 · Base color
- [ ] `Remielle_Origin_Body_1_M.png` — Outfit 1 · Material mask
- [ ] `Remielle_Origin_Body_1_N.png` — Outfit 1 · Normal and lighting
- [ ] `Remielle_Origin_Body_2_A.png` — Outfit 2 · Auxiliary channels
- [ ] `Remielle_Origin_Body_2_D.png` — Outfit 2 · Base color
- [ ] `Remielle_Origin_Body_2_M.png` — Outfit 2 · Material mask
- [ ] `Remielle_Origin_Body_2_N.png` — Outfit 2 · Normal and lighting

**Weapon and wings**

- [ ] `Remielle_Ramiel_Wings_M.png` — Wings · Material mask
- [ ] `Remielle_Ramiel_Wings_N.png` — Wings · Normal and lighting
- [ ] `Remielle_Wings_A.png` — Wings · Auxiliary channels
- [ ] `Remielle_Wings_D.png` — Wings · Base color

**MatCap**

- [ ] `Eff_MatCap_019.png` — MatCap 019 · Outfit 2
- [ ] `Eff_MatCap_077.png` — MatCap 077 · Outfit 2
- [ ] `Eff_Matcap_092.png` — MatCap 092 · Outfit 2
- [ ] `Eff_Matcap_112.png` — MatCap 112 · Outfit 2
- [ ] `Eff_Matcap_115.png` — MatCap 115 · Outfit 1

### Reference extras (27)

**Geometry inputs**

- [ ] `Remielle_Wings.json` — Remielle_Wings · Source mesh
- [ ] `Remielle_Hair.json` — Remielle_Hair · Source mesh
- [ ] `Remielle_Origin_Body_1.json` — Remielle_Origin_Body_1 · Source mesh
- [ ] `Remielle_Origin_Body_2.json` — Remielle_Origin_Body_2 · Outline mesh
- [ ] `Remielle_Origin_Body_1_native_inputs.npz` — Remielle_Origin_Body_1 · Surface vertices
- [ ] `Remielle_Origin_Body_2_outline_inputs.npz` — Remielle_Origin_Body_2 · Surface vertices
- [ ] `Remielle_Wings_native_inputs.npz` — Remielle_Wings · Surface vertices
- [ ] `Remielle_face_inputs.npz` — Remielle_Face · Face vertices
- [ ] `original_eye_inputs.npz` — Remielle_Face · Eye vertices
- [ ] `Remielle_caster.npz` — Face shadow receiver
- [ ] `face_outline.npz` — Face outline vertices

**Color and fine detail**

- [ ] `Remielle_character_lut.exr` — Character color LUT

**Mip atlases**

- [ ] `Remielle_Origin_Body_2_D_mips_f32.exr` — Outfit 2 · Base color
- [ ] `Remielle_Origin_Body_2_N_mips_f32.exr` — Outfit 2 · Normal and lighting
- [ ] `Remielle_Origin_Body_2_M_mips_f32.exr` — Outfit 2 · Material mask
- [ ] `Remielle_Origin_Body_2_A_mips_f32.exr` — Outfit 2 · Auxiliary channels
- [ ] `Remielle_Origin_Body_1_D_mips_f32.exr` — Outfit 1 · Base color
- [ ] `Remielle_Origin_Body_1_N_mips_f32.exr` — Outfit 1 · Normal and lighting
- [ ] `Remielle_Origin_Body_1_M_mips_f32.exr` — Outfit 1 · Material mask
- [ ] `Remielle_Origin_Body_1_A_mips_f32.exr` — Outfit 1 · Auxiliary channels
- [ ] `Remielle_Wings_D_mips.exr` — Wings · Base color
- [ ] `Remielle_Ramiel_Wings_N_mips.exr` — Wings · Normal and lighting
- [ ] `Remielle_Ramiel_Wings_M_mips.exr` — Wings · Material mask
- [ ] `Remielle_Wings_A_mips.exr` — Wings · Auxiliary channels

**Scrolling background**

- [ ] `large.png` — Large lettering
- [ ] `small.png` — Small lettering
- [ ] `screen_shade.png` — Background shading

## Velina

The eye lookup is `Eye_E_02.png`. The base build recovered the tangent basis for the second body group and both weapon meshes, plus the red vertex channel for the hair shadow caster. For a face shadow or fringe mismatch, check the secondary-UV FBX and the face and eye files for this outfit.

### Base files (26)

**Model and idle**

- [ ] `Avatar_Female_Size03_Velina_UI.fbx` — FBX
- [ ] `Avatar_Female_Size03_Velina_Ani_UI_CharSel_Loop.anim` — Idle

**Face and eyes**

- [ ] `Eye_E_02.png` — Eye color lookup
- [ ] `Female_Face_Lightmap_02.png` — Face lightmap
- [ ] `Velina_Face_D.png` — Face · Base color

**Hair**

- [ ] `Velina_Hair_Map_A.png` — Hair · Auxiliary channels
- [ ] `Velina_Hair_Map_D.png` — Hair · Base color
- [ ] `Velina_Hair_Map_M.png` — Hair · Material mask
- [ ] `Velina_Hair_Map_N.png` — Hair · Normal and lighting

**Outfit**

- [ ] `Velina_Body_Map1_A.png` — Outfit 1 · Auxiliary channels
- [ ] `Velina_Body_Map1_D.png` — Outfit 1 · Base color
- [ ] `Velina_Body_Map1_M.png` — Outfit 1 · Material mask
- [ ] `Velina_Body_Map1_N.png` — Outfit 1 · Normal and lighting
- [ ] `Velina_Body_Map2_A.png` — Outfit 2 · Auxiliary channels
- [ ] `Velina_Body_Map2_D.png` — Outfit 2 · Base color
- [ ] `Velina_Body_Map2_M.png` — Outfit 2 · Material mask
- [ ] `Velina_Body_Map2_N.png` — Outfit 2 · Normal and lighting

**Weapon and wings**

- [ ] `Velina_Weapon_Map_A.png` — Weapon · Auxiliary channels
- [ ] `Velina_Weapon_Map_D.png` — Weapon · Base color
- [ ] `Velina_Weapon_Map_M.png` — Weapon · Material mask
- [ ] `Velina_Weapon_Map_N.png` — Weapon · Normal and lighting

**MatCap**

- [ ] `Eff_MatCap_006.png` — MatCap 006 · Weapon
- [ ] `Eff_MatCap_021.png` — MatCap 021 · Outfit 1, Outfit 2, Hair, Weapon
- [ ] `Eff_MatCap_073.png` — MatCap 073 · Outfit 1
- [ ] `Eff_MatCap_121.png` — MatCap 121 · Outfit 1, Outfit 2
- [ ] `Eff_Matcap_Socks_03.png` — MatCap Socks_03 · Outfit 2

### Reference extras (31)

**Secondary UV**

- [ ] `uv.fbx` — UV FBX

**Geometry inputs**

- [ ] `Velina_Body1.json` — Velina_Body1 · Outline mesh
- [ ] `Velina_Body2.json` — Velina_Body2 · Outline mesh
- [ ] `Velina_Hair1.json` — Velina_Hair1 · Outline mesh
- [ ] `Velina_Hair2.json` — Velina_Hair2 · Outline mesh
- [ ] `Velina_Weapon1.json` — Velina_Weapon1 · Outline mesh
- [ ] `Velina_Weapon2.json` — Velina_Weapon2 · Outline mesh
- [ ] `Velina_Body1_outline_inputs.npz` — Velina_Body1 · Surface vertices
- [ ] `Velina_Body2_outline_inputs.npz` — Velina_Body2 · Surface vertices
- [ ] `Velina_Weapon2_outline_inputs.npz` — Velina_Weapon2 · Surface vertices
- [ ] `Velina_Weapon1_outline_inputs.npz` — Velina_Weapon1 · Surface vertices
- [ ] `Velina_face_inputs.npz` — Velina_Face · Face vertices
- [ ] `Velina_eye_inputs.npz` — Velina_Face · Eye vertices
- [ ] `Velina_caster.npz` — Face shadow receiver
- [ ] `face_outline.npz` — Face outline vertices

**Color and fine detail**

- [ ] `Velina_character_lut.exr` — Character color LUT

**Mip atlases**

- [ ] `Velina_Weapon_Map_D_mips_f32.exr` — Weapon · Base color
- [ ] `Velina_Weapon_Map_N_mips_f32.exr` — Weapon · Normal and lighting
- [ ] `Velina_Weapon_Map_M_mips_f32.exr` — Weapon · Material mask
- [ ] `Velina_Weapon_Map_A_mips_f32.exr` — Weapon · Auxiliary channels
- [ ] `Velina_Body_Map2_D_mips_f32.exr` — Outfit 2 · Base color
- [ ] `Velina_Body_Map2_N_mips_f32.exr` — Outfit 2 · Normal and lighting
- [ ] `Velina_Body_Map2_M_mips_f32.exr` — Outfit 2 · Material mask
- [ ] `Velina_Body_Map2_A_mips_f32.exr` — Outfit 2 · Auxiliary channels
- [ ] `Velina_Body_Map1_D_mips_f32.exr` — Outfit 1 · Base color
- [ ] `Velina_Body_Map1_N_mips_f32.exr` — Outfit 1 · Normal and lighting
- [ ] `Velina_Body_Map1_M_mips_f32.exr` — Outfit 1 · Material mask
- [ ] `Velina_Body_Map1_A_mips_f32.exr` — Outfit 1 · Auxiliary channels

**Scrolling background**

- [ ] `large.png` — Large lettering
- [ ] `small.png` — Small lettering
- [ ] `screen_shade.png` — Background shading

## Vivian

The translucent hair and gem materials reuse the second body texture set, so there is no separate hair texture field. The weapon textures are for the parasol. In the base build, back faces on the second body group use its verified primary UVs. For a wrong pattern under the canopy, check Vivian’s secondary-UV FBX and both weapon mesh JSONs. Surface detail uses `CharacterOverlayTex.png`.

### Base files (25)

**Model and idle**

- [ ] `Avatar_Female_Size02_Vivian_UI.fbx` — FBX
- [ ] `Avatar_Female_Size02_Vivian_Ani_UI_CharacterSelect_Loop_02.anim` — Idle

**Face and eyes**

- [ ] `Eye_E.png` — Eye color lookup
- [ ] `Female_Face_Lightmap.png` — Face lightmap
- [ ] `Vivian_Face_D.png` — Face · Base color

**Outfit**

- [ ] `Vivian_Body_1_A.png` — Outfit 1 · Auxiliary channels
- [ ] `Vivian_Body_1_D.png` — Outfit 1 · Base color
- [ ] `Vivian_Body_1_M.png` — Outfit 1 · Material mask
- [ ] `Vivian_Body_1_N.png` — Outfit 1 · Normal and lighting
- [ ] `Vivian_Body_2_A.png` — Outfit 2 · Auxiliary channels
- [ ] `Vivian_Body_2_D.png` — Outfit 2 · Base color
- [ ] `Vivian_Body_2_M.png` — Outfit 2 · Material mask
- [ ] `Vivian_Body_2_N.png` — Outfit 2 · Normal and lighting

**Weapon and wings**

- [ ] `Vivian_Weapon_A.png` — Weapon · Auxiliary channels
- [ ] `Vivian_Weapon_D.png` — Weapon · Base color
- [ ] `Vivian_Weapon_M.png` — Weapon · Material mask
- [ ] `Vivian_Weapon_N.png` — Weapon · Normal and lighting

**MatCap**

- [ ] `Eff_MatCap_021.png` — MatCap 021 · Outfit 1, Outfit 2, Hair
- [ ] `Eff_MatCap_035.png` — MatCap 035 · Weapon
- [ ] `Eff_MatCap_065.png` — MatCap 065 · Outfit 1
- [ ] `Eff_MatCap_077.png` — MatCap 077 · Outfit 1
- [ ] `Eff_MatCap_Gem.png` — MatCap Gem · Gem
- [ ] `Eff_Matcap_025.png` — MatCap 025 · Outfit 1
- [ ] `Eff_Matcap_Socks.png` — MatCap Socks · Hair
- [ ] `Eff_Matcap_Socks3.png` — MatCap Socks3 · Outfit 2

### Reference extras (18)

**Secondary UV**

- [ ] `uv.fbx` — UV FBX

**Geometry inputs**

- [ ] `Vivian_Weapon_02.json` — Vivian_Weapon_02 · Outline mesh
- [ ] `Vivian_Weapon_01.json` — Vivian_Weapon_01 · Outline mesh
- [ ] `Vivian_Body_2.json` — Vivian_Body_2 · Outline mesh
- [ ] `Vivian_Body_1.json` — Vivian_Body_1 · Outline mesh

**Color and fine detail**

- [ ] `lut.exr` — Character color LUT
- [ ] `CharacterOverlayTex.png` — Surface detail

**Mip atlases**

- [ ] `Vivian_Weapon_N_mips.exr` — Weapon · Normal and lighting
- [ ] `Vivian_Weapon_M_mips.exr` — Weapon · Material mask
- [ ] `Vivian_Weapon_D_mips.exr` — Weapon · Base color
- [ ] `Vivian_Weapon_A_mips.exr` — Weapon · Auxiliary channels
- [ ] `Vivian_Body_1_D_mips.exr` — Outfit 1 · Base color
- [ ] `Vivian_Body_1_N_mips.exr` — Outfit 1 · Normal and lighting
- [ ] `Vivian_Body_1_M_mips.exr` — Outfit 1 · Material mask
- [ ] `Vivian_Body_1_A_mips.exr` — Outfit 1 · Auxiliary channels

**Scrolling background**

- [ ] `large.png` — Large lettering
- [ ] `small.png` — Small lettering
- [ ] `screen_shade.png` — Background shading
