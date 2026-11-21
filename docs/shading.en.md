# Reconstructing the ZZZ outfit shader in Blender

[简体中文](shading.zh-CN.md) · [Back to the user guide](../README.md)

Start with one piece of cloth. Its base map supplies the color, the surface normal and light direction place the shadow, and the material ID selects the shadow tones and highlight response. Compare the game character target with Blender at the matching camera before judging the final image with its background and glow.

## 1 · Preserve the mesh inputs

Keep triangle order, material slots, split normals, vertex colors, skin weights and extra UV sets on export. The add-on assigns materials by slot and animation by bone path. For a meaningful pixel comparison, identify the same mesh, material, triangle and UV in both images. When the poses differ, start with the interior of a flat patch.

The tangent, signed bitangent and normal form a local basis. Decode the tangent-space normal, transport its basis through skinning and the object transform, then compare the resulting direction to the light. A weapon with nonuniform import scale needs different transformations for positions and normals. The optional surface streams are tied to the original vertex and triangle correspondence. See [model import and material assignment](../zzz_toon/character.py), [surface normals](../zzz_toon/native_surface.py) and [UV routing](../zzz_toon/surface_uv.py).

## 2 · Wire the texture inputs

In Blender's Shader Editor, trace back from Material Output. **Data** in this table means **Non-Color**. Use **Channel Packed** alpha for packed textures so that image loading preserves independent channel values.

| Input | How to read it | Where it goes |
| --- | --- | --- |
| D / `_MainTex` | sRGB color, decoded to linear | Base color for skin, cloth, hair and weapons. Eye and some transparent variants also read alpha. |
| N / `_LightTex` on body and hair | Data | RG encodes tangent-space normal XY. The shader reconstructs Z where needed. B shifts the shadow boundary, with about 0.5 neutral. A standard normal-map node alone loses the separate shadow input. |
| M / `_OtherDataTex` | Data | R values around 1.0, 0.7, 0.5, 0.3 and 0.0 select five parameter sets. G drives metal response and B masks ordinary specular. |
| A / `_OtherDataTex2` | Data | G can affect rim width. B gates MatCap or emission in supported variants. The verified translucent-cloth shader uses R for coverage. The active variant determines each channel's meaning. |
| Face lightmap | Data | The face program samples a directional shadow boundary using the head frame and light. It can occupy the same named light-texture slot as the body's packed normal. |
| Eye lookup | Data, 16×16 | Split the vertex red byte into two four-bit coordinates and fetch one texel. The eye shader combines the resulting RGB and alpha. |
| MatCap | Color, assigned per material slot | Look up the reflection color from the view-space direction. Some variants also use auxiliary UVs. The preset supplies tint, mask and additive or interpolated blending. |
| LUT, mip atlas, fine detail | Separate named slots | The LUT is a linear floating-point strip encoding a 3D color transform. The atlas contains mip levels. Fine detail is enabled only by its material variant. |

Each of the five material IDs can select different shallow-shadow, deep-shadow, reflection and outline colors. In the GAME path, native shallow and deep shadow properties are decoded to linear using the verified material conversion. HDR specular and rim colors retain their original range. Already-linear values should pass through once. See [material color decoding](../zzz_toon/material_inputs.py) and the [node builders](../zzz_toon/core.py).

## 3 · Build the body, then the other material families

1. **Cloth and skin.** Sample D, select the parameter set with M.R, and evaluate signed `dot(N, L)` in world space. Keep the negative half. Apply the vertex-alpha shadow adjustment and the N.B bias. In the reference body path, the latter contributes `2 × (2B − 1)`. The per-ID smoothness controls transitions through deep shadow, shallow shadow and light. Apply skin or non-skin tints and the environment contribution. Remielle's white top makes a good boundary test. Velina's blue bodice exposes changes in shadow hue.
2. **Metal and accessories.** Inspect ordinary specular and MatCap separately. Specular uses the surface normal, view direction, light direction and per-ID parameters. M.B masks its contribution and M.G changes both reflection and diffuse response. MatCap supplies a second signal from its own UV and mask. When a bright spot moves, inspect the deformed tangent basis and viewing angle before editing its color.
3. **Hair, wings and weapons.** Hair can have its own key direction and a head-bone sphere that shapes highlight normals. The fringe also exports a transmission signal for the eye composite. Wings, ribbons, fan and parasol use individual material slots. Two-sided materials can read another UV set on the back face. If a parasol loses its pattern, verify its slots and D/N/M/A assignments first. See [light profiles](../zzz_toon/light_profiles.py) and [head-sphere normals](../zzz_toon/native_head_sphere.py).
4. **Face, eyes, brows and lashes.** Use head-space lighting and the face lightmap to place the face shadow. Matching auxiliary streams can restore draw-specific vertex inputs and receiver coverage. Eyes and lashes have separate opacity. Supported presets combine premultiplied facial features with fringe transmission. Velina's obscured eye is a useful visibility check. Vivian's lashes and jaw expose outline aliasing. EEVEE layer coverage approximates the game's depth and stencil passes. See [face shading](../zzz_toon/native_face.py) and [feature blending](../zzz_toon/native_face_layers.py).
5. **Outlines, rim and transparent cloth.** An outline is an expanded shell with a direction, width, depth offset and color. Rim is a lighting term that responds to view, key direction and material masks. The verified cloth branch writes premultiplied color and coverage, which are resolved over the underlying character. Check thin edges against the actual background to see their coverage. See [outline construction](../zzz_toon/outline_runtime.py) and [transparent cloth](../zzz_toon/transparent_layers.py).

## 4 · Sample and composite at the output scale

**Choose the mip at the camera.** Biased sampling derives a footprint from the projected triangle's UV gradients and combines the shader and sampler biases. One source texture can therefore use different mips at the close and full-body stops. A supplied authored atlas preserves its stored levels. Given only the base texture, the add-on builds a linear chain. Narrow highlights and distant patterns can differ between the two chains. See [biased sampling](../zzz_toon/native_sampling.py).

**Follow the draw order.** On the matched preview paths, process the opaque character and its LUT, resolve the enabled rim, facial features and transparent passes, then compute Bloom and place the result over the scrolling page. Transparent cloth has its own LUT and premultiplication step. The final operation uses `foreground RGB + background RGB × (1 − foreground alpha)`, preserving premultiplied character color and glow. Background motion follows timeline time at both camera distances. See [character LUT](../zzz_toon/native_color.py), [character composite](../zzz_toon/character.py), [Bloom](../zzz_toon/native_bloom.py) and [scrolling background](../zzz_toon/wardrobe_page.py).

## Local reference inputs

| Optional file | Contract and fallback |
| --- | --- |
| Secondary UV FBX | Matches the primary mesh and polygon corners. Without it, the available UVs on the main FBX are used. |
| Character LUT | A linear EXR strip, N² wide and N high. These presets use N=32. The captured DDS row zero corresponds to the bottom UV row of the strip. An empty field synthesizes the strip from a fitted numeric model. Supplying a local strip overrides that model. |
| Mip atlas | A W×2W linear atlas for a square source of width W. Level zero occupies the bottom square and each subsequent level halves in size. In its absence the source is downsampled. |
| Surface, outline, face and eye files | Each slot accepts its named original mesh JSON or a project-generated NPZ with the expected arrays. The relevant loaders check positions, triangle indices or UV correspondence. Empty slots trigger extraction of verified tangent, color and UV streams from the matching FBX where available. Remaining parts use base shading. |
| Background and fine detail | One seamless color image gives a scrolling page. For the original layout supply the large lettering, small lettering and shading plate. Missing page layers use original procedural art. The fine-detail input belongs to the material that requested it. |

## 5 · Locate a mismatch

Hold camera, pose and light fixed and change one input at a time. Check silhouette and occlusion, then base color, then shadow weight. Isolate ordinary specular, MatCap and composition last. Matching base color at the same UV with a moving shadow boundary points to normals, vertex colors or skinning. A matching character target with a different final page suggests inspecting color space, LUT and Bloom order. Check role-specific adjustments after a discrepancy repeats for the same outfit.

A GPU capture identifies the bindings, constants and intermediate values for one draw. A recording shows whether the result holds up during motion. Device sampling, real-time AA, secondary motion and per-role video color adjustments have different sources of uncertainty. Wardrobe-specific appearance adjustments live in the [role presets](../zzz_toon/wardrobe_data) and are applied only to the selected outfit.

See the [public input audit](inputs.en.md) for automatic reconstruction, local overrides and remaining dependencies in each outfit preset.
