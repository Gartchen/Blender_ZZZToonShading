"""Original procedural lettering and studio vignette for the three-layer page."""
import bpy
import numpy as np

# Original block letters, evaluated as masks in page coordinates.
LETTERS = {
    'O': (14, 17, 17, 17, 17, 17, 14), 'U': (17, 17, 17, 17, 17, 17, 14),
    'T': (31, 4, 4, 4, 4, 4, 4), 'F': (31, 16, 16, 30, 16, 16, 16),
    'I': (31, 4, 4, 4, 4, 4, 31), 'P': (30, 17, 17, 30, 16, 16, 16),
    'R': (30, 17, 17, 30, 20, 18, 17), 'E': (31, 16, 16, 30, 16, 16, 31),
    'V': (17, 17, 17, 17, 10, 10, 4), 'W': (17, 17, 17, 21, 21, 21, 10),
}


def layer(key):
    w, h = {'large': (1024, 180), 'small': (1024, 64), 'screen_shade': (512, 288)}[key]
    x, y = np.meshgrid((np.arange(w) + .5) / w, (np.arange(h) + .5) / h)
    if key == 'screen_shade':
        radius = ((x - .50) / .66) ** 2 + ((y - .51) / .85) ** 2
        value = .024 * np.maximum(0., 1 - radius) ** 1.5
    else:
        word = 'OUTFIT' if key == 'large' else 'PREVIEW'
        repeats = 1 if key == 'large' else 2
        columns = 6 * len(word)
        gx = np.floor(((x * repeats) % 1.) * columns).astype(int)
        gy = np.floor((y - .06) * 7 / .88).astype(int)
        rows = np.array([LETTERS[c] for c in word])
        bit = 1 << np.maximum(0, 4 - gx % 6)
        ink = ((rows[gx // 6, np.clip(gy, 0, 6)] & bit) != 0)
        ink &= (gx % 6 < 5) & (gy >= 0) & (gy < 7)
        value = ink * (.78 + .22 * (((x * w + y * h) % 6) < 4))
    rgba = np.ones((h, w, 4), dtype=np.float32)
    rgba[..., :3] = value[..., None]
    image = bpy.data.images.new('Studio page • ' + key, w, h, alpha=True, float_buffer=True, is_data=True)
    image.file_format = 'OPEN_EXR'
    image.colorspace_settings.name = 'Non-Color'
    image.pixels.foreach_set(rgba.ravel())
    image.update()
    image.pack()
    return image
