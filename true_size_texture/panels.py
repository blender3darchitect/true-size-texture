import bpy
from bpy.types import Panel
from mathutils import Vector

_SCALE_EPSILON = 0.0001


class TST_PT_MainPanel(Panel):
    bl_idname = "VIEW3D_PT_tst_main"
    bl_label = "True Size Texture"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "True Size"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.tst_settings

        # Texture dimensions
        box = layout.box()
        box.label(text="Texture Dimensions", icon='TEXTURE')
        col = box.column(align=True)
        if settings.non_square:
            col.prop(settings, "texture_width")
            col.prop(settings, "texture_height")
        else:
            col.prop(settings, "texture_size")
        box.prop(settings, "non_square")

        layout.separator()

        # Blend slider
        layout.prop(settings, "projection_blend", slider=True)

        # Coordinate type and alignment.
        # Non-square mode sizes in the node graph and always uses object
        # coordinates, so the choice does not apply there.
        row = layout.row()
        row.active = not settings.non_square
        row.prop(settings, "coord_type")
        layout.prop(settings, "align_to_edge")

        layout.separator()

        # PBR Maps
        box = layout.box()
        box.label(text="PBR Maps", icon='NODE_MATERIAL')
        col = box.column(align=True)
        col.prop(settings, "image_base_color", text="Base Color")
        col.prop(settings, "image_roughness", text="Roughness")
        col.prop(settings, "image_bump", text="Bump")
        col.prop(settings, "image_displacement", text="Displacement")

        # Calibration placeholder: generated at the current texture size,
        # assigned straight to Base Color so it is one click to a testable
        # material.
        col.separator()
        row = col.row(align=True)
        row.prop(settings, "placeholder_resolution", text="")
        row.operator("tst.create_placeholder", icon='IMAGE_DATA')

        layout.separator()

        # Scale warning
        eligible = [
            obj for obj in context.selected_objects
            if obj.type in {'MESH', 'CURVE', 'SURFACE'}
        ]
        bad_scale = [
            obj for obj in eligible
            if (obj.scale - Vector((1.0, 1.0, 1.0))).length > _SCALE_EPSILON
        ]
        if bad_scale:
            row = layout.row()
            row.alert = True
            row.label(
                text=f"{len(bad_scale)} object(s) have unapplied scale",
                icon='ERROR',
            )

        # Apply buttons
        layout.operator("tst.apply_mapping", icon='NODE_MATERIAL')

        # Separate & Apply — only shown in Edit Mode
        if context.mode == 'EDIT_MESH':
            layout.operator(
                "tst.separate_and_apply",
                icon='MOD_EDGESPLIT',
            )

    @classmethod
    def poll(cls, context):
        return bool(context.selected_objects)


classes = (TST_PT_MainPanel,)
