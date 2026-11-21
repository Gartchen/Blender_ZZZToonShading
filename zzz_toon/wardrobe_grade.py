"""Smooth wardrobe color response fitted to the three verified preview targets."""
import numpy as np

MODEL = 'wardrobe-log-grade-v1'
# Per-channel toe, scale, exponent, cubic shape and two chroma corrections.
COEFFICIENTS = (
    (.0388146, .1623777, 2.227196, -.1999062, -.4971674, 4.0660804, -.7557303, -.0335246, .5120636, .047645),
    (.0359009, .540285, 2.7062778, -2.2391472, 3.0956677, -.5418941, -.2127096, -.0312229, .1085177, .0589339),
    (.0398335, .15313, 2.162978, .494432, -.590657, 3.950716, .5346821, .0142217, -.2011571, -.0130165),
)


def image(size):
    import bpy
    for im in bpy.data.images:
        if im.get('zzz_wardrobe_grade') == MODEL and tuple(im.size) == (size * size, size):
            return im
    axis = np.linspace(0., 1., size, dtype=np.float32)
    green, blue, red = np.meshgrid(axis, axis, axis, indexing='ij')
    low = np.minimum(np.minimum(red, green), blue)
    high = np.maximum(np.maximum(red, green), blue)
    rgba = np.ones((size, size, size, 4), dtype=np.float32)
    for channel, value in enumerate((red, green, blue)):
        toe, gain, power, a, b, c, d, e, f, g = COEFFICIENTS[channel]
        x = 2 * np.maximum(value - toe, 0.)
        base = gain * x ** power * (1 + x * (a + x * (b + x * c)))
        upper, lower = high - value, value - low
        chroma = 1 + upper * (d + upper * f) + lower * (e + lower * g)
        rgba[..., channel] = np.clip(base * chroma, 0., 1.)
    im = bpy.data.images.new('Wardrobe preview • procedural grade', size * size, size,
                              alpha=True, float_buffer=True, is_data=True)
    im.file_format = 'OPEN_EXR'
    im.colorspace_settings.name = 'Non-Color'
    im.alpha_mode = 'CHANNEL_PACKED'
    im.use_half_precision = False
    im.pixels.foreach_set(rgba.ravel())
    im.update()
    im.pack()
    im['zzz_wardrobe_grade'] = MODEL
    return im
