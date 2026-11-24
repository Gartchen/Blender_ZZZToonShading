"""Resolve optional, locally owned capture streams for the animated wardrobe."""
from pathlib import Path


def resolve_support(config, common, request, generated):
    inputs, prepare = config['inputs'], config['prepare']
    # The common LUT is shared in the original profiles. Each wardrobe chooses
    # its own local LUT below, including when the caller supplies a replacement.
    common.pop('native_character_lut', None)
    omitted = []

    def prune(table, purpose, fields=('path',)):
        for name, spec in list(table.items()):
            if any(not Path(spec[key]).is_file() for key in fields):
                del table[name]
                omitted.append(name + ' / ' + purpose)

    prune(prepare.get('replace_mesh', {}), 'source topology')
    prepare.pop('texture', None)  # The explicit texture slot always takes precedence.
    # Animated outfits consume the rest-mesh outline. The captured posed
    # silhouette is only meaningful in a static capture camera.
    if prepare.get('rest_outline'):
        inputs['native_outline'] = prepare['rest_outline']
    prune(inputs.get('native_outline', {}).get('mesh_inputs', {}), 'outline stream')
    surface = inputs.get('native_surface', {})
    for spec in surface.get('mesh_inputs', {}).values():
        if Path(spec['path']).is_file() and not Path(spec['source']).is_file():
            spec['source'] = request['model']
    prune(surface.get('mesh_inputs', {}), 'surface basis')
    if not surface.get('mesh_inputs'):
        inputs.pop('native_surface', None)
    face = inputs.get('native_face', {})
    prune(face.get('mesh_inputs', {}), 'face attributes')
    if not face.get('mesh_inputs'):
        inputs.pop('native_face', None)
    elif face.get('receiver') and not Path(face['receiver']['red_path']).is_file():
        face.pop('receiver')
    prune(inputs.get('native_face_layers', {}).get('eye_inputs', {}), 'explicit eye indices')
    lut = inputs.get('native_character_lut', {})
    if lut and not Path(lut['texture']).is_file():
        from .wardrobe_grade import MODEL
        if lut.get('fallback_model') == MODEL:
            lut.pop('texture')
            lut.update(source_format=MODEL, source='Fitted analytic wardrobe grade', scope='Three verified wardrobe presets')
            generated.append('Parametric character grade')
        else:
            inputs.pop('native_character_lut')
            omitted.append('character LUT')
    if inputs.get('character_overlay') and not Path(inputs['character_overlay']).is_file():
        inputs.pop('character_overlay')
        omitted.append('surface overlay')
    derived = {name for material in inputs['native_sampling']['materials'].values()
               for name, item in material.items() if item.get('derive_mips')}
    if derived:
        generated.append(f'{len(derived)} texture mip chains')
    if not request.get('background'):
        extra = request.get('extras', {})
        page = Path(__file__).with_name('wardrobe_data')
        for key in ('large', 'small', 'screen_shade'):
            if not Path(extra.get(key, page/(key + '.png'))).is_file():
                generated.append('Studio page / ' + key)
    return {'generated': sorted(generated), 'fallbacks': sorted(set(omitted)), 'local_overrides': sorted(request.get('extras', {}))}
