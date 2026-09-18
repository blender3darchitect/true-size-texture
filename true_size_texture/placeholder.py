"""Generate labelled calibration textures for checking texture placement.

The swatch mirrors the classic printed reference: a light field inside a
heavy black border, with the real-world width written along the top edge
and the height down the left. Applied through this add-on at the same
size, the labels should read upright and undistorted on every face — if
the mapping is wrong, the wrong number shows up on the wrong axis, which
a checker pattern would never reveal.

Two passes build the image:

1. A pixel pass (border, field, corner tick) using numpy. This has no
   dependencies beyond Blender itself and always succeeds.
2. A text pass through blf drawing into a GPU offscreen buffer, read
   back and composited over the field. This needs a GPU context, so it
   degrades gracefully: without it the swatch still carries its border,
   aspect and corner tick, just no numbers.

The image is packed into the .blend rather than written to disk, so the
add-on needs no file permissions and leaves nothing behind.
"""
import bpy
import numpy as np

# Blender's built-in font. Portable — no system font path to guess at.
_FONT_ID = 0

# sRGB values written straight into the byte buffer (Blender stores
# image.pixels 1:1 with the bytes for a non-float image, so these are
# display values, not linear ones).
_FIELD_VALUE = 0.918   # #EAEAEA, the light grey of the reference swatch
_INK_VALUE = 0.0       # border, tick and text

# Fractions of the image's shorter side
_BORDER_FRACTION = 0.035
_TICK_FRACTION = 0.077     # corner orientation mark, ~2.2x the border
_TICK_GAP_FRACTION = 0.022  # gap between border and tick
_TEXT_FRACTION = 0.11      # label cap height
_TEXT_GAP_FRACTION = 0.02  # breathing room inside the border

# Height label sits above centre, matching the reference swatch
_SIDE_LABEL_HEIGHT = 0.45

_MIN_SIDE_PX = 64


def resolve_dimensions(width_m, height_m, resolution):
    """Pixel size for a swatch of this real-world shape.

    The longer real-world side gets the full resolution and the shorter
    one is scaled to match, so texel density is uniform. A square image
    for an 18x36 texture would render the border twice as thick on one
    axis, which reads as a mapping bug when it is only the swatch.
    """
    if width_m >= height_m:
        width_px = resolution
        height_px = max(
            _MIN_SIDE_PX, int(round(resolution * height_m / width_m)),
        )
    else:
        height_px = resolution
        width_px = max(
            _MIN_SIDE_PX, int(round(resolution * width_m / height_m)),
        )
    return width_px, height_px


def _render_text_layer(width_px, height_px, labels):
    """Rasterise labels to a coverage array, or None if no GPU context.

    `labels` are (text, size_px, h_align, v_align, x, y) with y measured
    from the bottom, matching Blender's image row order. Positions are
    resolved here rather than by the caller because blf only reports
    usable metrics once a GPU context exists.
    """
    try:
        import blf
        import gpu
        from mathutils import Matrix
    except ImportError:
        return None

    # A background Blender has no GPU context until asked for one.
    # Harmless in the UI, where a context already exists.
    if bpy.app.background and hasattr(gpu, "init"):
        try:
            gpu.init()
        except Exception:
            return None

    try:
        offscreen = gpu.types.GPUOffScreen(width_px, height_px)
    except Exception:
        return None

    try:
        with offscreen.bind():
            framebuffer = gpu.state.active_framebuffer_get()
            framebuffer.clear(color=(0.0, 0.0, 0.0, 1.0))

            # blf draws in pixel coordinates, so map [0..w]x[0..h] to NDC
            with gpu.matrix.push_pop():
                gpu.matrix.load_matrix(Matrix.Identity(4))
                gpu.matrix.load_projection_matrix(
                    Matrix.Diagonal(
                        (2.0 / width_px, 2.0 / height_px, 1.0, 1.0)
                    ).to_4x4()
                    @ Matrix.Translation(
                        (-width_px / 2.0, -height_px / 2.0, 0.0)
                    )
                )
                blf.color(_FONT_ID, 1.0, 1.0, 1.0, 1.0)
                for text, size_px, h_align, v_align, x, y in labels:
                    blf.size(_FONT_ID, size_px)
                    text_w, text_h = blf.dimensions(_FONT_ID, text)
                    draw_x = x - text_w / 2.0 if h_align == 'CENTER' else x
                    draw_y = y - text_h if v_align == 'TOP' else y
                    blf.position(_FONT_ID, draw_x, draw_y, 0.0)
                    blf.draw(_FONT_ID, text)

            buffer = framebuffer.read_color(
                0, 0, width_px, height_px, 4, 0, 'FLOAT',
            )
    except Exception:
        return None
    finally:
        offscreen.free()

    # White-on-black, so the red channel is the coverage mask
    pixels = np.asarray(buffer, dtype=np.float32).reshape(
        height_px, width_px, 4,
    )
    return pixels[..., 0:1].copy()


def _draw_swatch(width_px, height_px, labels):
    """Build the RGBA float array for one swatch."""
    short_side = min(width_px, height_px)
    border = max(2, int(round(short_side * _BORDER_FRACTION)))
    border = min(border, short_side // 3)

    pixels = np.empty((height_px, width_px, 4), dtype=np.float32)
    pixels[..., :3] = _FIELD_VALUE
    pixels[..., 3] = 1.0

    pixels[:border, :, :3] = _INK_VALUE
    pixels[height_px - border:, :, :3] = _INK_VALUE
    pixels[:, :border, :3] = _INK_VALUE
    pixels[:, width_px - border:, :3] = _INK_VALUE

    # Corner tick at the UV origin (bottom-left), so orientation stays
    # readable when the labels are too small to make out. Inset from the
    # border, or it merges with it and reads as a notch rather than a mark.
    tick = max(3, int(round(short_side * _TICK_FRACTION)))
    inset = border + max(2, int(round(short_side * _TICK_GAP_FRACTION)))
    pixels[inset:inset + tick, inset:inset + tick, :3] = _INK_VALUE

    coverage = _render_text_layer(width_px, height_px, labels)
    if coverage is not None:
        # Text is _INK_VALUE (black), so compositing is a simple fade
        pixels[..., :3] *= (1.0 - coverage)

    return pixels, coverage is not None


def generate_placeholder(
    width_m, height_m, resolution, name, format_length,
):
    """Create (or refresh) a packed calibration image.

    `format_length` turns a length in metres into a display string, so
    unit formatting stays in one place in operators.py rather than being
    duplicated here.

    Returns (image, has_labels). has_labels is False when no GPU context
    was available and the swatch carries no text.
    """
    width_px, height_px = resolve_dimensions(width_m, height_m, resolution)

    short_side = min(width_px, height_px)
    border = max(2, int(round(short_side * _BORDER_FRACTION)))
    text_size = max(8, int(round(short_side * _TEXT_FRACTION)))
    gap = int(round(short_side * _TEXT_GAP_FRACTION))

    labels = [
        # Width across the top, centred
        (
            format_length(width_m), text_size, 'CENTER', 'TOP',
            width_px / 2.0, height_px - border - gap,
        ),
        # Height down the left, above centre
        (
            format_length(height_m), text_size, 'LEFT', 'BASELINE',
            border + gap * 2, height_px * _SIDE_LABEL_HEIGHT,
        ),
    ]

    pixels, has_labels = _draw_swatch(width_px, height_px, labels)

    # Reuse the datablock when the shape matches so repeated clicks
    # refresh in place instead of piling up .001 duplicates
    image = bpy.data.images.get(name)
    if image is not None and tuple(image.size) != (width_px, height_px):
        image = None
    if image is None:
        image = bpy.data.images.new(name, width_px, height_px, alpha=False)

    image.colorspace_settings.name = 'sRGB'
    image.pixels.foreach_set(pixels.ravel())
    image.update()
    image.pack()

    return image, has_labels
