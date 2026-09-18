# True Size Texture

A Blender add-on for applying textures at their real-world size using box
(triplanar) mapping. Enter the physical dimensions of a texture — `1m`,
`18in`, `75cm` — and one click builds the shader nodes and configures each
object so one full texture repeat covers exactly that distance.

Built for architectural work, where a tile is 300mm because the real tile is
300mm, and resizing a wall must not resize its brickwork.

**Blender 4.2 or newer** (extension format).

## Why not just scale the Mapping node?

Because scaling the Mapping node ties the texture to the object. Stretch the
wall and the bricks stretch with it.

Instead, size is driven by each object's **Texture Space**, which is what
Blender's Generated coordinates are normalised against:

```
Generated = (vertex_pos - texspace_location) / (2 x texspace_size) + 0.5
```

Setting `texspace_size` to half the texture's real size makes one repeat span
exactly that distance in metres, independent of the object's dimensions. The
Mapping node stays at scale `(1, 1, 1)` and simply passes coordinates through.

This automates the manual workflow long shared in the Blender community; the
add-on just removes the bookkeeping.

## Features

- **Real-world sizing** in metres, centimetres, feet or inches — the field
  follows the scene's unit system, so type `18in` and Blender converts
- **PBR channels**: Base Color, Roughness, Bump and Displacement, each wired
  with the correct colour space
- **Converts existing materials** — imported or asset-library materials keep
  their images and get rewired for box mapping
- **Normal Maps become Bump nodes** automatically, since normal maps need
  UV-space tangent data and break under box projection
- **Non-square textures** are projected per face normal so an 18x36 texture
  reads correctly on every orientation, not just one
- **Align to Edge** starts tiles at an object's bounding box corner instead of
  centring them on the origin
- **Separate & Apply** splits selected faces into their own object with their
  own texture size, from Edit Mode
- **Calibration placeholders** — generate a labelled swatch at the current
  size to verify placement before the real texture goes on

## Installing

Download the latest `.zip` from
[Releases](../../releases), then in Blender:

**Edit > Preferences > Get Extensions > Install from Disk**

Or clone this repository and copy `true_size_texture/` into your Blender
extensions folder:

```
%APPDATA%\Blender Foundation\Blender\<version>\extensions\user_default\   (Windows)
~/.config/blender/<version>/extensions/user_default/                      (Linux)
~/Library/Application Support/Blender/<version>/extensions/user_default/  (macOS)
```

## Using it

The panel lives in the 3D Viewport sidebar (`N`) under the **True Size** tab.

1. Set **Size** to the texture's real-world dimensions. Enable **Non-Square**
   for separate width and height.
2. Pick your images under **PBR Maps**, or click **Create Placeholder** to
   generate a labelled test swatch.
3. Select your objects and click **Apply Box Mapping**.

**Apply object scale first** (`Ctrl+A > Scale`). Unapplied scale distorts the
result; the panel warns when it finds any, but never changes your objects.

### Blend

Box mapping picks a projection plane per face from its dominant normal axis.
Where two axes compete — a curve, a bevel, a 45° face — that choice flips, and
without blending the texture visibly jumps there. **Blend** cross-fades the
competing projections instead.

`0.2`–`0.3` is the useful range. At `0.0` you get hard seams on anything
curved; pushed high, the fade band widens until the texture appears doubled.

### Non-square textures

Blender's `BOX` projection offers no per-plane UV control, and `texspace_size`
is a single value per mesh — so a non-square texture cannot carry the correct
extent on every face orientation that way. With **Non-Square** enabled the
add-on instead samples each channel three times, once per projection plane, and
blends the samples by the object-space face normal:

| Faces pointing | Across | Up |
|---|---|---|
| ±X | Y | Z |
| ±Y | X | Z |
| ±Z | X | Y |

Square textures keep the native `BOX` path — fewer nodes and Blender's own
blending.

## Known limitations

- **Normal maps are converted to Bump**, by design. Normal Map nodes expect
  UV-space tangent data and are wrong under any box projection.
- **Rotating texture coordinates** via the Mapping node stretches the texture
  under box projection. Use a pre-rotated source image.
- **Non-square costs more nodes**: three samples per channel instead of one.
- **Blend is not numerically comparable** between the square and non-square
  paths — they use different mechanisms, so re-tune by eye after toggling.
- **Shared mesh data shares texture space.** Changing one object's size affects
  every object sharing that mesh.

The source is commented with the reasoning behind each path — see
`operators.py` for the node graphs and `placeholder.py` for the swatch
generation.

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).
