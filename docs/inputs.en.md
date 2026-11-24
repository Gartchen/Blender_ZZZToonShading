# Wardrobe inputs and material inspection

[简体中文](inputs.zh-CN.md) · [Installation and quick start](../README.md) · [Shader walkthrough](shading.en.md)

## Choosing inputs

| Input | How v0.52.0 uses it |
| --- | --- |
| Main FBX | Supplies the rig, mesh names, material slots, vertex colors and available UVs. Generic can scan and locate slots before you provide other files. |
| Idle animation | Use the matching Unity text `.anim` or parsed AnimationClip JSON to build a playable scene. Bone paths must match the rig. |
| Textures and material table | The three dedicated outfits expose named image slots. Generic reads the selected local material JSON and searches adjacent export folders for its image references. Assign a path manually for missing or ambiguous matches. |
| Secondary UVs and surface streams | A matching multi-UV FBX can recover face and back-face coordinates. Dedicated slots also accept the listed surface, eye, outline, LUT and mip files. Choose the original exports for the same model. |
| Background | Use the generated scrolling page, a seamless color image or the page-layer inputs exposed by the selected preset. |
| Wardrobe capture for Generic | Choose the FrameAnalysis directory containing the draws for the same model and outfit. The importer checks geometry and UVs before reading supported light, eye-index and fringe data. With assets alone, the preview uses an approximate wardrobe environment. |

For Remielle’s wing or body outlines, check the source mesh inputs. For Velina’s face, check the secondary UVs. For Vivian’s parasol, check the front and back UVs and the parasol texture slots. The three preset galleries use complete local inputs.

## Generic material slot inspector

In v0.52.0, **Generic** inspects the four tested wardrobe outfits through the same six shading paths. A model can vary in its colors, textures, MatCap assignments and captured light values. A slot that cannot be mapped to a shared part remains in the inventory. If its name is unfamiliar, expand the material and choose **Shared part** when you know its role. Clothing or hair can use the shared path while an extra effect is reported separately.

![Vivian Body 1 highlighted from the material slot list](media/generic-slot-inspector.jpg)

1. Select **Generic**, choose the main FBX, then click **Read FBX slots**. You can inspect the model before supplying any animation or textures. **All model slots** lists the mesh name, zero-based slot index and source material. Reused materials have a row for each assignment.
2. Select a row to see its details. Click the magnifier to center and highlight its faces in solid view. This works on excluded faces of a mixed mesh and on entire hidden accessories. The scanner shows the FBX rest pose. A generated scene shows the pose at the time you click. An empty slot has a row and a message. **Clear highlight** restores the previous view. The highlight is excluded from rendering.
3. Supply material files, images, an idle animation and, if available, a matching wardrobe capture. Use **Check input files** after editing paths. A required base color can be located and corrected here before building. Open the generated scene to inspect the saved shader bindings and draw evidence. **Show current input check** lets you check edits separately from the saved result.
4. Expand **Material table fields** to inspect scalar and color values. **Read** means the material builder consulted the field when preparing or constructing shader nodes. Some values control which nodes are created. **Used** on a texture means the image feeds the constructed surface or a feature output, or supplies the eye color baked into the mesh. A shader connection alone cannot measure its contribution to a particular pixel.

| Inspector result | What the evidence establishes |
| --- | --- |
| Located file | The image resolved on disk. The inspector lists its shader use separately. |
| Missing or ambiguous | A required or referenced file is unavailable, or more than one file matches. Assign the correct local file and check again. An empty optional binding has no file requirement. An omitted material table leaves its original bindings unverified. |
| Outside | The slot has no shared material assignment, or this particular texture or effect falls outside its part’s shared calculation. A declaration can exist on several characters. |
| Enabled in draw | A matching draw has the relevant switch enabled and the expected binding. For example, Vivian Body 1 draw 37 enables the extra overlay. |
| Inactive in draw | A verified switch is off or the sampled input is neutral. Remielle’s face draws 44 and 57 have an all-zero blush input across the captured mip levels. |
| No verified draw | The frame was omitted or the inspector could not associate a supported draw with this slot. Its activity remains unknown. |

The four test models contain **75 source slots** across shared surfaces, projection helpers and excluded geometry. The dedicated outfit presets cover Remielle, Velina and Vivian. Miyabi is a Generic validation model. The inspection covers the tested wardrobe frame and supported inputs. New uniforms, unknown passes and omitted game assets can require investigation before an effect can be identified.
