from . import properties, operators, panels
import bpy
from bpy.props import PointerProperty


def register():
    for cls in properties.classes:
        bpy.utils.register_class(cls)
    for cls in operators.classes:
        bpy.utils.register_class(cls)
    for cls in panels.classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.tst_settings = PointerProperty(
        type=properties.TST_PG_Settings
    )


def unregister():
    del bpy.types.Scene.tst_settings

    for cls in reversed(panels.classes):
        bpy.utils.unregister_class(cls)
    for cls in reversed(operators.classes):
        bpy.utils.unregister_class(cls)
    for cls in reversed(properties.classes):
        bpy.utils.unregister_class(cls)
