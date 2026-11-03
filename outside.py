"""Mask settings and the geometry cache shared by mask/linked outlines."""
import bpy
from array import array
from bpy.props import EnumProperty,FloatVectorProperty

_batches={}
_shader=None
CHUNK=64
SURFACES={'MESH','CURVE','SURFACE','FONT','META'}


def redraw():
    wm=bpy.context.window_manager
    if wm:
        for window in wm.windows:
            for area in window.screen.areas:
                if area.type=='VIEW_3D':area.tag_redraw()


def settings_changed(self,c):
    from . import silhouette
    silhouette.invalidate();redraw()


def mask_color_changed(self,c):
    redraw()


def shader():
    global _shader
    if _shader is None:
        import gpu
        _shader=gpu.shader.from_builtin('UNIFORM_COLOR')
    return _shader


def batch(obj,graph_key,visible_only=False):
    from gpu_extras.batch import batch_for_shader
    original=obj.original
    key=(graph_key,original.as_pointer(),visible_only)
    if key in _batches:return _batches[key][0]
    mesh=None;temporary=original.mode=='EDIT' or obj.type!='MESH'
    try:
        # Evaluated mesh data already contains modifiers and native tessellation.
        # Copy only for curve conversion or edit-mode synchronization.
        mesh=obj.to_mesh() if temporary else obj.data
        if not mesh or not mesh.vertices or not mesh.polygons:
            _batches[key]=(None,original.data.as_pointer() if original.data else 0);return None
        mesh.calc_loop_triangles()
        coordinates=array('f',[0.0])*(len(mesh.vertices)*3)
        indices=array('i',[0])*(len(mesh.loop_triangles)*3)
        mesh.vertices.foreach_get('co',coordinates)
        mesh.loop_triangles.foreach_get('vertices',indices)
        import numpy as np
        triangles=np.frombuffer(indices,dtype=np.int32).reshape(-1,3)
        if visible_only and original.mode=='EDIT':
            hidden=np.empty(len(mesh.polygons),dtype=np.bool_);mesh.polygons.foreach_get('hide',hidden)
            owners=np.empty(len(mesh.loop_triangles),dtype=np.int32);mesh.loop_triangles.foreach_get('polygon_index',owners)
            triangles=triangles[~hidden[owners]]
            if not len(triangles):
                _batches[key]=(None,original.data.as_pointer() if original.data else 0);return None
        result=batch_for_shader(shader(),'TRIS',{'pos':np.frombuffer(coordinates,dtype=np.float32).reshape(-1,3)},indices=triangles)
        _batches[key]=(result,original.data.as_pointer() if original.data else 0)
        return result
    finally:
        if temporary and mesh is not None:obj.to_mesh_clear()


def supported(c):
    shading=c.space_data.shading
    return shading.type!='WIREFRAME' and not (shading.type=='SOLID' and shading.show_xray)


def panel(layout,c):
    layout.prop(c.scene,'sg_outside_display')
    if c.scene.sg_outside_display=='MASK':
        from . import silhouette
        from .ui import color_row
        color_row(layout,c.scene,'sg_mask_rgba','遮罩颜色')
        if silhouette._error:layout.label(text='遮罩暂停：'+silhouette._error,icon='ERROR')


def register():
    bpy.types.Scene.sg_outside_display=EnumProperty(name='组外显示',items=(('NORMAL','无','保持原显示',0),('MASK','轮廓遮罩','按当前组网格轮廓保留清晰区域，不修改材质',3)),default='MASK',update=settings_changed)
    bpy.types.Scene.sg_mask_rgba=FloatVectorProperty(name='遮罩颜色',description='RGB 控制颜色，Alpha 控制不透明度：0 为透明，1 为完全覆盖',subtype='COLOR',size=4,min=0,max=1,default=(0,0,0,.5),update=mask_color_changed)


def unregister():
    global _shader
    _batches.clear();_shader=None
    for prop in ('sg_outside_display','sg_mask_rgba'):delattr(bpy.types.Scene,prop)
