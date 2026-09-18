import bpy
from bpy.types import Operator
from mathutils import Vector

from . import placeholder

# Tolerance for comparing scale to (1, 1, 1)
_SCALE_EPSILON = 0.0001

# Tolerance for unit rounding, e.g. recognising a whole number of feet
_UNIT_EPSILON = 1e-6

# Label prefix on every node this add-on builds, so a re-run can tear
# down its own previous output without touching hand-made nodes.
_TST_TAG = "TST:"

# PBR channels, in panel order:
#   (picker attribute, target socket, intermediate node, colour space)
# 'DISPLACEMENT' channels target the Material Output, the rest the
# Principled BSDF.
_TRIPLANAR_CHANNELS = (
    ('image_base_color', 'Base Color', None, 'sRGB'),
    ('image_roughness', 'Roughness', None, 'Non-Color'),
    ('image_bump', 'Normal', 'BUMP', 'Non-Color'),
    ('image_displacement', 'Displacement', 'DISPLACEMENT', 'Non-Color'),
)


def _has_unapplied_scale(obj):
    """Check if object scale differs from (1, 1, 1)."""
    ref = Vector((1.0, 1.0, 1.0))
    return (obj.scale - ref).length > _SCALE_EPSILON


def _find_node(nodes, node_type):
    """Find first node of given type in a node tree."""
    for node in nodes:
        if node.type == node_type:
            return node
    return None


def _ensure_node(nodes, bl_type, fallback_label=None):
    """Get existing node of bl_type or create one."""
    for node in nodes:
        if node.bl_idname == bl_type:
            return node, False
    node = nodes.new(type=bl_type)
    if fallback_label:
        node.label = fallback_label
    return node, True


def _remove_orphaned_nodes(nodes, links):
    """Remove nodes that have no output connections after conversion.

    Cleans up UVMap and other coordinate nodes left disconnected
    when their connections are replaced by the TexCoord -> Mapping chain.
    """
    # Node types that are only useful as coordinate sources
    orphan_types = {'UVMAP', 'TEX_COORD', 'MAPPING'}
    removed = 0

    for node in list(nodes):
        if node.type not in orphan_types:
            continue
        # Check if any output socket has a connection
        has_output = any(
            link.from_node == node for link in links
        )
        if not has_output:
            nodes.remove(node)
            removed += 1

    return removed


def _replace_normal_maps_with_bump(nodes, links):
    """Replace Normal Map nodes with Bump nodes.

    Normal Map nodes don't work with box projection because they expect
    UV-space tangent data. Bump nodes use height and work correctly.

    For each Normal Map node found:
    - Create a Bump node at the same location
    - Rewire: whatever fed Normal Map "Color" -> Bump "Height"
    - Rewire: Bump "Normal" -> wherever Normal Map "Normal" was connected
    - Remove the Normal Map node
    """
    normal_maps = [n for n in nodes if n.type == 'NORMAL_MAP']
    replaced = 0

    for nm_node in normal_maps:
        bump = nodes.new(type='ShaderNodeBump')
        bump.location = nm_node.location

        # Rewire input: find what feeds Normal Map "Color" input
        for link in list(links):
            if link.to_node == nm_node and link.to_socket.name == 'Color':
                links.new(link.from_socket, bump.inputs['Height'])

        # Rewire output: find where Normal Map "Normal" connects
        for link in list(links):
            if link.from_node == nm_node and link.from_socket.name == 'Normal':
                links.new(bump.outputs['Normal'], link.to_socket)

        nodes.remove(nm_node)
        replaced += 1

    return replaced


def _create_pbr_image_node(
    nodes, links, mapping, image, label, y_pos, color_space='Non-Color',
):
    """Create a BOX-projected Image Texture node for a PBR channel."""
    image_tex = nodes.new(type='ShaderNodeTexImage')
    image_tex.location = (-250, y_pos)
    image_tex.label = label
    image_tex.image = image
    image_tex.projection = 'BOX'
    image_tex.extension = 'REPEAT'

    # Set color space on the image data-block
    if image.colorspace_settings.name != color_space:
        image.colorspace_settings.name = color_space

    # Wire Mapping -> Vector input
    links.new(mapping.outputs['Vector'], image_tex.inputs['Vector'])

    return image_tex


def _incoming(links, node, socket_name):
    """Return the node feeding node.inputs[socket_name], or None."""
    for link in links:
        if link.to_node == node and link.to_socket.name == socket_name:
            return link.from_node
    return None


def _trace_image(links, node, depth=8):
    """Walk input links backwards to the first Image Texture node.

    Used to recover a channel's image whatever sits in between — a Bump
    node, a Displacement node, or a previous triplanar Mix chain — so
    rebuilding never loses textures the user already assigned.
    """
    if node is None or depth <= 0:
        return None
    if node.type == 'TEX_IMAGE':
        return node
    for link in links:
        if link.to_node == node:
            found = _trace_image(links, link.from_node, depth - 1)
            if found is not None:
                return found
    return None


def _resolve_channels(links, principled, mat_output, settings):
    """Decide each channel's image and collect the nodes it replaces.

    The panel picker wins; otherwise the image already feeding that
    channel is reused, which is what makes converting an imported
    material preserve its textures.

    Returns (images, stale) where images maps the picker attribute name
    to an Image or None, and stale is the set of Image Texture / Bump /
    Displacement nodes currently feeding those channels.
    """
    images = {}
    stale = set()

    for attr, socket_name, via, _colorspace in _TRIPLANAR_CHANNELS:
        target = mat_output if via == 'DISPLACEMENT' else principled
        if target is None:
            images[attr] = getattr(settings, attr)
            continue

        feeding = _incoming(links, target, socket_name)
        if feeding is not None and feeding.type in {'BUMP', 'DISPLACEMENT'}:
            stale.add(feeding)

        tex = _trace_image(links, feeding)
        if tex is not None:
            stale.add(tex)

        images[attr] = getattr(settings, attr) or (
            tex.image if tex is not None else None
        )

    return images, stale


def _clear_tagged(nodes, extra=()):
    """Remove every node this add-on built, plus anything in `extra`.

    Only nodes labelled with _TST_TAG are owned by the add-on, so
    hand-made nodes elsewhere in the tree are left alone.
    """
    removed = 0
    for node in list(nodes):
        if node.label.startswith(_TST_TAG) or node in extra:
            nodes.remove(node)
            removed += 1
    return removed


def _has_tagged(nodes):
    """True if a previous add-on build is present in this tree."""
    return any(node.label.startswith(_TST_TAG) for node in nodes)


def _blend_to_exponent(blend):
    """Map the 0-1 Blend slider to a triplanar weight exponent.

    Native BOX blending is a socket on the Image Texture node; a manual
    triplanar blends by raising |normal| to a power before normalising,
    where a high exponent means a narrow transition band. Squared so
    most of the slider travel lands in the useful low-blend range:
    blend 0.0 -> 64 (near-hard seams), 0.25 -> ~36, 1.0 -> 1 (smooth).
    """
    return 1.0 + (1.0 - blend) ** 2 * 63.0


def _mix_sockets(node):
    """Resolve a ShaderNodeMix node's colour sockets.

    ShaderNodeMix carries same-named sockets for every data type
    (Float / Vector / Color / Rotation), so a bare name lookup is
    ambiguous and a positional index lookup breaks between Blender
    versions. Match on name AND socket type instead.
    """
    factor = next(
        s for s in node.inputs if s.name == 'Factor' and s.type == 'VALUE'
    )
    a_in = next(s for s in node.inputs if s.name == 'A' and s.type == 'RGBA')
    b_in = next(s for s in node.inputs if s.name == 'B' and s.type == 'RGBA')
    result = next(
        s for s in node.outputs if s.name == 'Result' and s.type == 'RGBA'
    )
    return factor, a_in, b_in, result


def _build_triplanar(mat, settings, obj):
    """Non-square path: manual triplanar driven by the face normal.

    Blender's BOX projection gives no per-plane UV control, and
    texspace_size is one tuple per mesh, so a non-square texture cannot
    carry the right extent on every face orientation that way. Here each
    face is sampled once per projection plane and the three samples are
    blended by the object-space face normal, so every orientation gets
    the texture's true width and height:

        faces facing +/-X  ->  UV = (Y / width, Z / height)
        faces facing +/-Y  ->  UV = (X / width, Z / height)
        faces facing +/-Z  ->  UV = (X / width, Y / height)

    Sizing happens in node math here, so Texture Space is unused and
    settings.coord_type does not apply — object coordinates are always
    used, which keeps the texture anchored to the object the way the
    square path does.
    """
    node_tree = mat.node_tree
    nodes = node_tree.nodes
    links = node_tree.links

    _replace_normal_maps_with_bump(nodes, links)

    principled = _find_node(nodes, 'BSDF_PRINCIPLED')
    if not principled:
        principled = nodes.new(type='ShaderNodeBsdfPrincipled')
        principled.location = (100, 300)
        out = _find_node(nodes, 'OUTPUT_MATERIAL')
        if out:
            links.new(principled.outputs['BSDF'], out.inputs['Surface'])
    mat_output = _find_node(nodes, 'OUTPUT_MATERIAL')

    # Harvest before tearing down — the images live on the nodes removed
    images, stale = _resolve_channels(links, principled, mat_output, settings)
    _clear_tagged(nodes, stale)

    width_m, height_m = _get_texture_dimensions(settings)
    # One repeat per width_m across U and per height_m across V
    uv_scale = (1.0 / width_m, 1.0 / height_m, 1.0)

    def _new(bl_type, name, location):
        node = nodes.new(type=bl_type)
        node.label = _TST_TAG + name
        node.location = location
        return node

    # --- Shared: object-space coordinates ---
    tex_coord = _new('ShaderNodeTexCoord', "Coords", (-1900, 200))
    coords = tex_coord.outputs['Object']

    if settings.align_to_edge:
        # Shift the origin to the bounding box minimum so a full tile
        # starts at the object's edge, matching the square path's use of
        # texspace_location.
        bb_min = Vector(obj.bound_box[0])
        edge = _new('ShaderNodeVectorMath', "Edge Offset", (-1700, 200))
        edge.operation = 'SUBTRACT'
        links.new(coords, edge.inputs[0])
        edge.inputs[1].default_value = (bb_min.x, bb_min.y, bb_min.z)
        coords = edge.outputs['Vector']

    sep_pos = _new('ShaderNodeSeparateXYZ', "Split Coords", (-1520, 200))
    links.new(coords, sep_pos.inputs['Vector'])

    # --- Shared: one scaled UV vector per projection plane ---
    # (plane axis, horizontal source, vertical source, layout y)
    planes = (
        ('X', 'Y', 'Z', 440),
        ('Y', 'X', 'Z', 220),
        ('Z', 'X', 'Y', 0),
    )
    plane_uv = {}
    for axis, u_axis, v_axis, y in planes:
        combine = _new(
            'ShaderNodeCombineXYZ', axis + " Plane UV", (-1340, y),
        )
        links.new(sep_pos.outputs[u_axis], combine.inputs['X'])
        links.new(sep_pos.outputs[v_axis], combine.inputs['Y'])

        scale = _new(
            'ShaderNodeVectorMath', axis + " Plane Scale", (-1160, y),
        )
        scale.operation = 'MULTIPLY'
        links.new(combine.outputs['Vector'], scale.inputs[0])
        scale.inputs[1].default_value = uv_scale
        plane_uv[axis] = scale.outputs['Vector']

    # --- Shared: blend weights from the object-space face normal ---
    geometry = _new('ShaderNodeNewGeometry', "Normal", (-1900, -460))
    to_object = _new(
        'ShaderNodeVectorTransform', "Normal to Object", (-1700, -460),
    )
    to_object.vector_type = 'NORMAL'
    to_object.convert_from = 'WORLD'
    to_object.convert_to = 'OBJECT'
    links.new(geometry.outputs['Normal'], to_object.inputs['Vector'])

    abs_n = _new('ShaderNodeVectorMath', "Abs Normal", (-1520, -460))
    abs_n.operation = 'ABSOLUTE'
    links.new(to_object.outputs['Vector'], abs_n.inputs[0])

    sharpen = _new('ShaderNodeVectorMath', "Sharpen", (-1340, -460))
    sharpen.operation = 'POWER'
    links.new(abs_n.outputs['Vector'], sharpen.inputs[0])
    exponent = _blend_to_exponent(settings.projection_blend)
    sharpen.inputs[1].default_value = (exponent, exponent, exponent)

    sep_w = _new('ShaderNodeSeparateXYZ', "Split Weights", (-1160, -460))
    links.new(sharpen.outputs['Vector'], sep_w.inputs['Vector'])

    # Two chained Mix nodes reproduce the normalised weighted sum
    # exactly, with no separate normalise step:
    #   mix(mix(sX, sY, fY), sZ, fZ) == (sX*wX + sY*wY + sZ*wZ) / sum
    # when fY = wY / (wX + wY) and fZ = wZ / (wX + wY + wZ).
    # Blender's Divide returns 0 on a zero divisor, so an axis-aligned
    # normal (wX = wY = 0) degrades to picking the Z sample cleanly.
    sum_xy = _new('ShaderNodeMath', "wX + wY", (-980, -400))
    sum_xy.operation = 'ADD'
    links.new(sep_w.outputs['X'], sum_xy.inputs[0])
    links.new(sep_w.outputs['Y'], sum_xy.inputs[1])

    factor_y = _new('ShaderNodeMath', "Factor Y", (-800, -400))
    factor_y.operation = 'DIVIDE'
    links.new(sep_w.outputs['Y'], factor_y.inputs[0])
    links.new(sum_xy.outputs['Value'], factor_y.inputs[1])

    sum_all = _new('ShaderNodeMath', "wX + wY + wZ", (-980, -620))
    sum_all.operation = 'ADD'
    links.new(sum_xy.outputs['Value'], sum_all.inputs[0])
    links.new(sep_w.outputs['Z'], sum_all.inputs[1])

    factor_z = _new('ShaderNodeMath', "Factor Z", (-800, -620))
    factor_z.operation = 'DIVIDE'
    links.new(sep_w.outputs['Z'], factor_z.inputs[0])
    links.new(sum_all.outputs['Value'], factor_z.inputs[1])

    # --- One three-sample chain per PBR channel ---
    plan = [
        channel for channel in _TRIPLANAR_CHANNELS
        if images.get(channel[0]) is not None
    ]
    if not plan:
        # Nothing assigned yet — build an empty Base Color chain so the
        # user has somewhere to drop an image, matching the BOX fallback
        plan = [_TRIPLANAR_CHANNELS[0]]

    for slot, (attr, socket_name, via, colorspace) in enumerate(plan):
        image = images.get(attr)
        target = mat_output if via == 'DISPLACEMENT' else principled
        if target is None:
            continue

        if image is not None and image.colorspace_settings.name != colorspace:
            image.colorspace_settings.name = colorspace

        base_y = 400 - slot * 820
        samples = []
        for step, axis in enumerate(('X', 'Y', 'Z')):
            tex = _new(
                'ShaderNodeTexImage',
                "{} {}".format(socket_name, axis),
                (-900, base_y - step * 250),
            )
            tex.image = image
            tex.projection = 'FLAT'
            tex.extension = 'REPEAT'
            links.new(plane_uv[axis], tex.inputs['Vector'])
            samples.append(tex.outputs['Color'])

        mix_xy = _new(
            'ShaderNodeMix',
            "{} Mix XY".format(socket_name),
            (-580, base_y - 120),
        )
        mix_xy.data_type = 'RGBA'
        mix_xy.blend_type = 'MIX'
        factor, a_in, b_in, xy_out = _mix_sockets(mix_xy)
        links.new(factor_y.outputs['Value'], factor)
        links.new(samples[0], a_in)
        links.new(samples[1], b_in)

        mix_z = _new(
            'ShaderNodeMix',
            "{} Mix Z".format(socket_name),
            (-400, base_y - 240),
        )
        mix_z.data_type = 'RGBA'
        mix_z.blend_type = 'MIX'
        factor, a_in, b_in, z_out = _mix_sockets(mix_z)
        links.new(factor_z.outputs['Value'], factor)
        links.new(xy_out, a_in)
        links.new(samples[2], b_in)

        if via == 'BUMP':
            bump = _new('ShaderNodeBump', "Bump", (-200, base_y - 240))
            links.new(z_out, bump.inputs['Height'])
            links.new(bump.outputs['Normal'], target.inputs[socket_name])
        elif via == 'DISPLACEMENT':
            disp = _new(
                'ShaderNodeDisplacement', "Displacement",
                (-200, base_y - 240),
            )
            links.new(z_out, disp.inputs['Height'])
            links.new(
                disp.outputs['Displacement'], target.inputs[socket_name],
            )
        else:
            links.new(z_out, target.inputs[socket_name])

    _remove_orphaned_nodes(nodes, links)


def _setup_box_mapping(mat, settings, obj):
    """Configure real-world mapping on a material.

    Dispatches between the two projection strategies. See the module
    docstring note on why non-square needs a different one:
    Blender's BOX projection exposes no per-plane UV control and
    texspace_size is a single tuple per mesh, so a non-square texture
    cannot get the right extent on every face orientation that way.
    """
    if settings.non_square:
        _build_triplanar(mat, settings, obj)
    else:
        _setup_box_projection(mat, settings)


def _setup_box_projection(mat, settings):
    """Square path: Blender's native BOX projection.

    Size comes from the object's Texture Space, not from these nodes.

    Three modes of operation:
    1. Convert existing: find all Image Textures, set BOX projection,
       wire to shared Mapping chain, replace Normal Map -> Bump
    2. Create from image pickers: for each non-empty picker, create
       an Image Texture and wire to the correct Principled BSDF input
    3. Fallback: create empty Image Texture -> Base Color
    """
    node_tree = mat.node_tree
    nodes = node_tree.nodes
    links = node_tree.links

    # --- Replace Normal Map nodes with Bump nodes ---
    _replace_normal_maps_with_bump(nodes, links)

    # --- Ensure Principled BSDF exists ---
    principled = _find_node(nodes, 'BSDF_PRINCIPLED')
    if not principled:
        principled = nodes.new(type='ShaderNodeBsdfPrincipled')
        principled.location = (100, 300)
        out = _find_node(nodes, 'OUTPUT_MATERIAL')
        if out:
            links.new(
                principled.outputs['BSDF'],
                out.inputs['Surface'],
            )

    # --- Tear down a previous triplanar build, if any ---
    # Toggling Non-Square off would otherwise leave three samples per
    # channel behind, all of which the conversion step below would
    # happily turn into duplicate BOX textures. Harvest the images
    # first so nothing the user assigned is lost.
    images, _stale = _resolve_channels(
        links, principled, _find_node(nodes, 'OUTPUT_MATERIAL'), settings,
    )
    if _has_tagged(nodes):
        _clear_tagged(nodes, _stale)

    # --- Find or create shared Texture Coordinate + Mapping nodes ---
    tex_coord, tc_new = _ensure_node(nodes, 'ShaderNodeTexCoord')
    mapping, map_new = _ensure_node(nodes, 'ShaderNodeMapping')

    if tc_new:
        tex_coord.location = (-800, 300)
    if map_new:
        mapping.location = (-550, 300)

    # Configure Mapping node
    mapping.vector_type = 'TEXTURE'
    mapping.inputs['Scale'].default_value = (1.0, 1.0, 1.0)

    # Wire Texture Coordinate -> Mapping (once)
    coord_output = settings.coord_type
    links.new(tex_coord.outputs[coord_output], mapping.inputs['Vector'])

    # --- Convert all existing Image Texture nodes to BOX ---
    existing_textures = [n for n in nodes if n.type == 'TEX_IMAGE']
    for image_tex in existing_textures:
        image_tex.projection = 'BOX'
        image_tex.projection_blend = settings.projection_blend
        image_tex.extension = 'REPEAT'
        links.new(mapping.outputs['Vector'], image_tex.inputs['Vector'])

    # --- Create PBR nodes from the resolved channel images ---
    # Only create nodes for channels that have an image (picked, or
    # harvested from the torn-down build) AND don't already have an
    # Image Texture wired to that input.

    mat_output = _find_node(nodes, 'OUTPUT_MATERIAL')

    # Check which Principled inputs already have Image Textures connected
    def _input_has_image_tex(input_name):
        for link in links:
            if (link.to_node == principled
                    and link.to_socket.name == input_name
                    and link.from_node.type == 'TEX_IMAGE'):
                return True
        return False

    # Base Color
    if images['image_base_color'] and not _input_has_image_tex('Base Color'):
        tex = _create_pbr_image_node(
            nodes, links, mapping,
            images['image_base_color'], "Base Color",
            y_pos=300, color_space='sRGB',
        )
        tex.projection_blend = settings.projection_blend
        links.new(tex.outputs['Color'], principled.inputs['Base Color'])

    # Roughness
    if images['image_roughness'] and not _input_has_image_tex('Roughness'):
        tex = _create_pbr_image_node(
            nodes, links, mapping,
            images['image_roughness'], "Roughness",
            y_pos=0, color_space='Non-Color',
        )
        tex.projection_blend = settings.projection_blend
        links.new(tex.outputs['Color'], principled.inputs['Roughness'])

    # Bump -> Bump node -> Principled "Normal"
    if images['image_bump'] and not _input_has_image_tex('Normal'):
        tex = _create_pbr_image_node(
            nodes, links, mapping,
            images['image_bump'], "Bump",
            y_pos=-300, color_space='Non-Color',
        )
        tex.projection_blend = settings.projection_blend
        bump_node = nodes.new(type='ShaderNodeBump')
        bump_node.location = (-50, -300)
        links.new(tex.outputs['Color'], bump_node.inputs['Height'])
        links.new(bump_node.outputs['Normal'], principled.inputs['Normal'])

    # Displacement -> Displacement node -> Material Output "Displacement"
    if images['image_displacement'] and mat_output:
        # Check if Material Output Displacement already has an Image Texture
        has_disp = False
        for link in links:
            if (link.to_node == mat_output
                    and link.to_socket.name == 'Displacement'):
                has_disp = True
                break
        if not has_disp:
            tex = _create_pbr_image_node(
                nodes, links, mapping,
                images['image_displacement'], "Displacement",
                y_pos=-600, color_space='Non-Color',
            )
            tex.projection_blend = settings.projection_blend
            disp_node = nodes.new(type='ShaderNodeDisplacement')
            disp_node.location = (100, -600)
            links.new(tex.outputs['Color'], disp_node.inputs['Height'])
            links.new(
                disp_node.outputs['Displacement'],
                mat_output.inputs['Displacement'],
            )

    # --- Fallback: if still no Image Textures, create empty one ---
    if not [n for n in nodes if n.type == 'TEX_IMAGE']:
        image_tex = nodes.new(type='ShaderNodeTexImage')
        image_tex.location = (-250, 300)
        image_tex.projection = 'BOX'
        image_tex.projection_blend = settings.projection_blend
        image_tex.extension = 'REPEAT'
        links.new(mapping.outputs['Vector'], image_tex.inputs['Vector'])
        links.new(
            image_tex.outputs['Color'], principled.inputs['Base Color'],
        )

    # --- Clean up orphaned nodes (e.g., UVMap from USDZ imports) ---
    _remove_orphaned_nodes(nodes, links)


def _get_texture_dimensions(settings):
    """Return (width_m, height_m) based on square/non-square mode."""
    if settings.non_square:
        return settings.texture_width, settings.texture_height
    return settings.texture_size, settings.texture_size


def _setup_texture_space(obj, settings):
    """Set real-world texture size via Texture Space.

    texspace_size is half-extents: Blender measures the radius from
    center to edge. So a 0.75m texture needs texspace_size = 0.375.
    Formula: texspace_size = texture_size_meters / 2.0

    When align_to_edge is enabled, texspace_location is calculated so
    that a full tile starts at the object's bounding box minimum corner.

    From the Generated coordinate formula:
        Generated = (vertex_pos - location) / (2 * size) + 0.5

    For Generated = 0 at bbox_min:
        location = bbox_min + size

    This is the critical step from the manual workflow — it decouples
    texture size from object dimensions.

    No-op in non-square mode: the triplanar graph sizes in node math and
    never reads Generated coordinates, so writing texspace here would be
    dead state — and could clobber another material on the same mesh that
    does rely on it.
    """
    if settings.non_square:
        return

    mesh = obj.data
    # Force initialization by reading first (Blender lazy-init gotcha)
    _ = mesh.texspace_size

    width_m, height_m = _get_texture_dimensions(settings)
    half_w = width_m / 2.0
    half_h = height_m / 2.0
    # X = width, Y = height, Z = width
    size = (half_w, half_h, half_w)

    mesh.use_auto_texspace = False
    mesh.texspace_size = size

    if settings.align_to_edge:
        # bound_box is 8 corners in local space; index 0 is the minimum
        bb_min = Vector(obj.bound_box[0])
        mesh.texspace_location = (
            bb_min.x + half_w,
            bb_min.y + half_h,
            bb_min.z + half_w,
        )
    else:
        mesh.texspace_location = (0.0, 0.0, 0.0)


def _length_unit(context, *lengths_m):
    """Pick a display unit that suits every given length.

    Returns (suffix, convert) where convert(metres) -> a number in that
    unit. Deciding across all the lengths at once is what keeps a pair
    formatted consistently ("18x36in", never "18in x 3ft").

    - Imperial: feet when every length is a whole number of feet, else
      inches
    - Metric: centimetres when the first length is under a metre or the
      scene is set to centimetres, else metres
    - None: metres
    """
    unit_system = context.scene.unit_settings.system
    length_unit = context.scene.unit_settings.length_unit

    if unit_system == 'IMPERIAL':
        # Compare against whole feet with a tolerance: 2ft round-trips
        # through metres as 24.000000000000004 inches, and an exact
        # modulo test would quietly demote it to "24in"
        feet = [metres / 0.3048 for metres in lengths_m]
        if all(
            f >= 1.0 - _UNIT_EPSILON and abs(f - round(f)) < _UNIT_EPSILON
            for f in feet
        ):
            return "ft", lambda metres: metres / 0.3048
        return "in", lambda metres: metres / 0.0254

    if length_unit == 'CENTIMETERS' or (
        unit_system == 'METRIC' and lengths_m[0] < 1.0
    ):
        return "cm", lambda metres: metres * 100

    return "m", lambda metres: metres


def _length_formatter(context, *lengths_m):
    """Return a formatter rendering every given length in one unit.

    Use this wherever several lengths are read together — a swatch's two
    labels, a material name — so they stay comparable. Formatting each
    separately would label an 18x36in texture "18in" across and "3ft"
    down, which are the same unit system but not the same unit.
    """
    suffix, convert = _length_unit(context, *lengths_m)
    return lambda metres: f"{convert(metres):g}{suffix}"


def _format_length(context, metres):
    """Format a single length for display, e.g. "18in", "75cm", "1.5m"."""
    return _length_formatter(context, metres)(metres)


def _format_size_label(context, settings):
    """Format texture dimensions as a compact string for material names.

    Square sizes collapse to one value ("-75cm"), non-square spell out
    both ("-18x36in").
    """
    width_m, height_m = _get_texture_dimensions(settings)
    suffix, convert = _length_unit(context, width_m, height_m)

    width_s = f"{convert(width_m):g}"
    height_s = f"{convert(height_m):g}"

    if width_s == height_s:
        return f"-{width_s}{suffix}"
    return f"-{width_s}x{height_s}{suffix}"


class TST_OT_ApplyMapping(Operator):
    bl_idname = "tst.apply_mapping"
    bl_label = "Apply Box Mapping"
    bl_description = "Set up real-world sized box mapping on selected objects"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return any(
            obj.type in {'MESH', 'CURVE', 'SURFACE'}
            for obj in context.selected_objects
        )

    def execute(self, context):
        settings = context.scene.tst_settings

        # Warn about unapplied scale
        bad_scale = [
            obj.name for obj in context.selected_objects
            if obj.type in {'MESH', 'CURVE', 'SURFACE'}
            and _has_unapplied_scale(obj)
        ]
        if bad_scale:
            names = ", ".join(bad_scale[:5])
            if len(bad_scale) > 5:
                names += f" (+{len(bad_scale) - 5} more)"
            self.report(
                {'WARNING'},
                f"Unapplied scale on: {names}. "
                "Apply scale (Ctrl+A) for correct texture sizing"
            )

        count = 0
        for obj in context.selected_objects:
            if obj.type not in {'MESH', 'CURVE', 'SURFACE'}:
                continue

            # Create material if none exists
            if not obj.data.materials:
                size_label = _format_size_label(context, settings)
                mat = bpy.data.materials.new(
                    name=f"TST_BoxMapped{size_label}",
                )
                mat.use_nodes = True
                obj.data.materials.append(mat)
            else:
                mat = obj.active_material
                if mat is None:
                    mat = obj.data.materials[0]
                if not mat.use_nodes:
                    mat.use_nodes = True

            _setup_box_mapping(mat, settings, obj)

            # Always configure texture space: disable auto and set (1,1,1)
            # so texture size stays fixed regardless of object dimensions.
            # This is the critical step from the manual workflow.
            _setup_texture_space(obj, settings)

            count += 1

        self.report({'INFO'}, f"Applied box mapping to {count} object(s)")
        return {'FINISHED'}


class TST_OT_SeparateAndApply(Operator):
    bl_idname = "tst.separate_and_apply"
    bl_label = "Separate & Apply"
    bl_description = (
        "Separate selected faces into a new object and apply "
        "box mapping with the specified texture size"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return (
            context.mode == 'EDIT_MESH'
            and context.active_object is not None
            and context.active_object.type == 'MESH'
        )

    def execute(self, context):
        settings = context.scene.tst_settings
        original_obj = context.active_object

        # Check that faces are selected (not just verts/edges)
        import bmesh
        bm = bmesh.from_edit_mesh(original_obj.data)
        selected_faces = [f for f in bm.faces if f.select]
        if not selected_faces:
            self.report({'WARNING'}, "No faces selected")
            return {'CANCELLED'}
        bm.free()

        # Separate selected faces into a new object
        bpy.ops.mesh.separate(type='SELECTED')

        # Switch to Object Mode to access the new object
        bpy.ops.object.mode_set(mode='OBJECT')

        # The new object is the last selected object that isn't the original
        new_obj = None
        for obj in context.selected_objects:
            if obj != original_obj and obj.type == 'MESH':
                new_obj = obj
                break

        if new_obj is None:
            self.report({'ERROR'}, "Separation failed")
            bpy.ops.object.mode_set(mode='EDIT')
            return {'CANCELLED'}

        # Create and apply material with texture size in name
        size_label = _format_size_label(context, settings)
        mat = bpy.data.materials.new(name=f"TST_BoxMapped{size_label}")
        mat.use_nodes = True

        # Clear existing materials and assign the new one
        new_obj.data.materials.clear()
        new_obj.data.materials.append(mat)

        _setup_box_mapping(mat, settings, new_obj)
        _setup_texture_space(new_obj, settings)

        # Name the new object after the texture size
        new_obj.name = f"{original_obj.name}{size_label}"

        self.report(
            {'INFO'},
            f"Separated and applied box mapping to '{new_obj.name}'",
        )

        # Return to Edit Mode on the original object
        context.view_layer.objects.active = original_obj
        bpy.ops.object.mode_set(mode='EDIT')

        return {'FINISHED'}


class TST_OT_CreatePlaceholder(Operator):
    bl_idname = "tst.create_placeholder"
    bl_label = "Create Placeholder"
    bl_description = (
        "Generate a labelled calibration texture at the current texture "
        "size and assign it to Base Color. Swap it for the real texture "
        "once the placement reads correctly"
    )
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = context.scene.tst_settings
        width_m, height_m = _get_texture_dimensions(settings)
        resolution = int(settings.placeholder_resolution)

        name = "TST_Placeholder{}_{}px".format(
            _format_size_label(context, settings), resolution,
        )

        # Both labels share one unit so the two numbers stay comparable
        image, has_labels = placeholder.generate_placeholder(
            width_m, height_m, resolution, name,
            _length_formatter(context, width_m, height_m),
        )
        settings.image_base_color = image

        if not has_labels:
            self.report(
                {'WARNING'},
                f"{image.name} created without labels "
                "(no GPU context available for text)",
            )
        else:
            self.report(
                {'INFO'},
                f"Created {image.name} "
                f"({image.size[0]}x{image.size[1]} px)",
            )
        return {'FINISHED'}


classes = (
    TST_OT_ApplyMapping,
    TST_OT_SeparateAndApply,
    TST_OT_CreatePlaceholder,
)
