# Generic mode limits and why full previews use outfit presets

[简体中文](generic-scope.zh-CN.md) · [Project overview](../README.md) · [How to use the inspector](inputs.en.md#how-to-read-the-results)

**Use Generic when you want to find the materials on a new model and see where its images are used. A tested, one-click preview of the whole outfit requires a dedicated preset in the current add-on.** The menu uses character names for convenience. Each preset is validated for one outfit. Another costume for the same character may have different meshes, textures and accessories.

## What do the four outfits share?

“Shared” refers to **shading operations** found to be identical or equivalent for the same kind of part in the four tested outfits. For example, both body groups use a material ID to select shadow and reflection settings. Remielle’s top and Velina’s bodice can use that calculation with their own colors, masks and reflection maps. A similar file name or texture count is insufficient evidence that two effects work the same way.

| Generic preview coverage | Scope of the evidence |
| --- | --- |
| Face, eyes, brows and lashes | Shared paths exist for face shading and shadow reception, eye color lookup and foreground feature blending. Exact eye or fringe correspondence also depends on matching triangles, corner UVs or indices to the original draw. |
| Hair and translucent fringe | The sampled outfits share a fringe transmission calculation based on view direction and an auxiliary map. The two original hair programs can still compute different colors even where the transmission matches. |
| Body 1 and Body 2 | Both use the common opaque body shading, material masks and MatCap reflections. Separate sheer cloth, weapons, wings and extra overlays can fall outside that scope. A mapped surface keeps its shared shading. An unmapped slot can still be located on the model. |

## Why can a full set of files still produce a different picture?

1. **Reading a resource and rendering its effect are separate steps.** In Vivian’s test capture, Body 1 enabled an extra color overlay in draw 37. Generic can report that switch and render the body’s base color and shadows. The overlay is outside the shared calculation. Rendering it also requires the blend operation that combines it with the body.
2. **The same image can map to different surfaces.** The front and back of Vivian’s parasol can use different UVs to select different patches of its texture. Complete image files still need the correct mesh data and front/back bindings. Similarly, an eye color chart needs an index for each relevant corner. Merged meshes, reordered vertices or lost UV sets can prevent a reliable match to a capture.
3. **A capture establishes activity for a particular draw.** On Remielle’s face, the blush input was zero in tested draws 44 and 57. Those draws added no blush. Other poses or outfits require their own evidence. An unmatched slot has unknown draw activity. If a capture is supplied and even the supported body draws cannot be matched to the model, the importer reports a match failure.
4. **A playable preview also needs scene-level decisions.** Body and hair can have different light directions. A fan, parasol or pair of wings changes the bounds for full-body framing. Face outlines depend on the mesh. An outfit preset stores the bindings, staging and applicable appearance adjustments that were checked together. For example, Vivian’s dedicated face color adjustment is scoped to her preview. Generic does not apply those coefficients to a new character.

## When does an outfit need its own entry?

| Goal | Appropriate approach |
| --- | --- |
| Inspect an unfamiliar model or preview the parts covered by the six shared groups | Use **Generic**. An unfamiliar slot name can be assigned a **Shared part** manually when its role is known. |
| Reproduce an outfit with existing shading operations but different slots, images or parameter values | Reuse the shader code. Create and validate the outfit’s bindings and parameter preset. Different values alone do not require another shader. |
| Reproduce an outfit with an unsupported draw effect | Establish the inputs and draw order, implement the missing operation, then enable it in the outfit preset. A menu entry or additional image file alone cannot perform that operation. |

Character selection is how the add-on exposes its validated outfits. **Inspecting a new character needs no new menu entry. Offering a one-click preview of its complete outfit calls for outfit-level configuration and testing.** Several presets can reuse a single implementation once the same behavior is verified across them.

## Current validation set

| Outfit sample | Source slots | Shared surface slots | Shadow helpers | Entirely outside scope |
| --- | ---: | ---: | ---: | ---: |
| Remielle | 31 | 8 | 1 | 22 |
| Velina | 13 | 9 | 1 | 3 |
| Vivian | 13 | 6 | 1 | 6 |
| Miyabi, default outfit | 18 | 9 | 1 | 8 |
| Total | **75** | **32** | **4** | **39** |

Counts refer to mesh/slot assignments in each FBX. If two meshes use the same material, each assignment is counted. A shared surface can also contain an extra effect. These counts describe inspection coverage. Appearance is assessed separately with camera-matched renders. The first three outfits also have dedicated presets. Miyabi is a Generic validation case.

See the [shared part and channel definitions](../zzz_toon/common_parts.py), [capture matching](../zzz_toon/generic_capture.py), [slot evidence and location](../zzz_toon/slot_inspector.py) and [dedicated outfit data](../zzz_toon/wardrobe_data).
