import bpy
from bpy.props import BoolProperty, FloatProperty, EnumProperty, PointerProperty
from bpy.types import PropertyGroup


class TST_PG_Settings(PropertyGroup):
    texture_size: FloatProperty(
        name="Texture Size",
        description="Real-world size of the texture (square)",
        default=1.0,
        min=0.001,
        soft_max=10.0,
        unit='LENGTH',
        subtype='DISTANCE',
    )
    non_square: BoolProperty(
        name="Non-Square",
        description="Use separate width and height for non-square textures",
        default=False,
    )
    texture_width: FloatProperty(
        name="Width",
        description="Real-world width of the texture (X/Z axes)",
        default=1.0,
        min=0.001,
        soft_max=10.0,
        unit='LENGTH',
        subtype='DISTANCE',
    )
    texture_height: FloatProperty(
        name="Height",
        description="Real-world height of the texture (Y axis)",
        default=1.0,
        min=0.001,
        soft_max=10.0,
        unit='LENGTH',
        subtype='DISTANCE',
    )
    projection_blend: FloatProperty(
        name="Blend",
        description="Box projection blend for seam smoothing (0.2-0.3 recommended)",
        default=0.25,
        min=0.0,
        max=1.0,
    )
    coord_type: EnumProperty(
        name="Coordinates",
        description="Coordinate source for square textures. Ignored when "
        "Non-Square is enabled, which always uses object coordinates "
        "because it sizes the texture in the node graph rather than in "
        "the object's texture space",
        items=[
            ('Generated', "Generated",
             "Uses texture space for consistent real-world sizing. "
             "Texture size stays fixed regardless of object scale"),
            ('Object', "Object",
             "World-scale coordinates from object origin. "
             "1 unit = 1 meter, but texture scales with object"),
        ],
        default='Generated',
    )
    align_to_edge: BoolProperty(
        name="Align to Anchor",
        description="Start a full tile at an anchor point instead of "
        "centering the texture on the object origin. Ignored when "
        "Coordinates is set to Object, which bypasses texture space",
        default=False,
    )
    align_anchor: EnumProperty(
        name="Anchor",
        description="Point the tile's corner is placed on when "
        "Align to Anchor is enabled",
        items=[
            ('BBOX', "Bounding Box",
             "Minimum corner of the object's bounding box"),
            ('ORIGIN', "Object Origin",
             "The object's own origin point"),
            ('CURSOR', "3D Cursor",
             "The 3D cursor's position, resolved into each object's "
             "local space. Read when the operator runs — moving the "
             "cursor afterwards has no effect until you apply again"),
        ],
        default='BBOX',
    )

    placeholder_resolution: EnumProperty(
        name="Resolution",
        description="Pixel resolution of the generated placeholder's "
        "longer side. The shorter side follows the real-world aspect so "
        "texel density stays uniform",
        items=[
            ('512', "512 px", "Fast to generate, fine for blocking out"),
            ('1024', "1024 px", "Default. Labels stay legible up close"),
            ('2048', "2048 px", "Crisp labels on large surfaces"),
            ('4096', "4096 px",
             "Print-scale detail. Slower to generate and needs a few "
             "hundred MB while it renders"),
        ],
        default='1024',
    )

    # PBR image pickers — Blender's native image data-block selector
    image_base_color: PointerProperty(
        type=bpy.types.Image,
        name="Base Color",
        description="Diffuse/albedo texture image",
    )
    image_roughness: PointerProperty(
        type=bpy.types.Image,
        name="Roughness",
        description="Roughness texture image (Non-Color)",
    )
    image_bump: PointerProperty(
        type=bpy.types.Image,
        name="Bump",
        description="Bump/height texture image (Non-Color). "
        "Used instead of Normal Map which is incompatible with box projection",
    )
    image_displacement: PointerProperty(
        type=bpy.types.Image,
        name="Displacement",
        description="Displacement texture image (Non-Color)",
    )


classes = (TST_PG_Settings,)
