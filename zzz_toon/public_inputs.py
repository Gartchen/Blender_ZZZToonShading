"""Resolve optional, locally owned capture streams for the animated wardrobe."""
from pathlib import Path


def resolve_support(config, common, request):
    inputs, prepare = config['inputs'], config['prepare']
    # The common LUT is shared in the original profiles. Each wardrobe chooses
    # its own local LUT below, including when the caller supplies a replacement.
    common.pop('native_character_lut', None)
    omitted = []

    def prune(table, fields=('path',)):
        for name, spec in list(table.items()):
            if any(not Path(spec[key]).is_file() for key in fields):
                del table[name]
                omitted.append(name)

    prune(prepare.get('replace_mesh', {}))
    prepare.pop('texture', None)  # The explicit texture slot always takes precedence.
    # Animated outfits consume the rest-mesh outline. The captured posed
    # silhouette is only meaningful in a static capture camera.
    if prepare.get('rest_outline'):
        inputs['native_outline'] = prepare['rest_outline']
    prune(inputs.get('native_outline', {}).get('mesh_inputs', {}))
    surface = inputs.get('native_surface', {})
    prune(surface.get('mesh_inputs', {}), ('path', 'source'))
    if not surface.get('mesh_inputs'):
        inputs.pop('native_surface', None)
    face = inputs.get('native_face', {})
    prune(face.get('mesh_inputs', {}))
    if not face.get('mesh_inputs'):
        inputs.pop('native_face', None)
    elif face.get('receiver') and not Path(face['receiver']['red_path']).is_file():
        face.pop('receiver')
    prune(inputs.get('native_face_layers', {}).get('eye_inputs', {}))
    lut = inputs.get('native_character_lut', {})
    if lut and not Path(lut['texture']).is_file():
        inputs.pop('native_character_lut')
        omitted.append('character LUT')
    if inputs.get('character_overlay') and not Path(inputs['character_overlay']).is_file():
        inputs.pop('character_overlay')
        omitted.append('surface overlay')
    return {'fallbacks': sorted(set(omitted)), 'local_overrides': sorted(request.get('extras', {}))}
