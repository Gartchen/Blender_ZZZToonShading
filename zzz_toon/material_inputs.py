"""Explicit material-property colour encoding, independent of textures/globals.

Only ShadowColor / ShallowColor were verified against GPU constants. HDR
SpecularColor and RimGlowLightColor must retain their original values.
"""
import copy

ENCODINGS = {'native-property', 'linear'}


def srgb_to_linear(value):
    return value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4


def prepare(params, game):
    if not params or not game:
        return params
    encoding = params.get('color_encoding', 'native-property')
    if encoding not in ENCODINGS:
        raise ValueError('Unknown material color_encoding: ' + str(encoding))
    if encoding == 'linear':
        return params
    out = copy.deepcopy(params)
    for key in ('ShadowColor', 'ShallowColor'):
        if key in out:
            out[key] = [tuple(srgb_to_linear(c) for c in rgba[:3]) + tuple(rgba[3:])
                        for rgba in out[key]]
    out['color_encoding'] = 'linear'
    return out
