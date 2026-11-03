"""Preferences own settings; the object context menu contains common actions."""
import bpy
from pathlib import Path
from bpy.utils import previews
from . import instances, outside, keymaps


_icons=None
SOCIAL_LINKS=(
    ('github','GitHub','https://github.com/RainskyCG?tab=repositories'),
    ('artstation','ArtStation','https://www.artstation.com/rain_sky'),
    ('bilibili','bilibili','https://space.bilibili.com/26918877'),
)


def about(layout):
    layout.label(text='作者：Rainsky')
    row=layout.box().row(align=True)
    row.scale_y=1.5
    for name,label,url in SOCIAL_LINKS:
        icon=_icons.get(name) if _icons else None
        options={'icon_value':icon.icon_id} if icon else {'icon':'URL'}
        row.operator('wm.url_open',text=label,**options).url=url


def color_row(layout,owner,property_name,label):
    row=layout.row(align=True)
    row.label(text=label+'：')
    row.prop(owner,property_name,text='')
    row.context_pointer_set('sg_color_owner',owner)
    reset=row.operator('wm.context_set_value',text='',icon='LOOP_BACK')
    reset.data_path='sg_color_owner.'+property_name
    reset.value=repr(tuple(owner.bl_rna.properties[property_name].default_array))


def preferences(layout, c, settings):
    layout.use_property_split=False
    layout.row(align=True).prop(settings,'preferences_tab',expand=True)
    if settings.preferences_tab=='KEYMAP':
        keymaps.draw(layout,c)
        return
    if settings.preferences_tab=='ABOUT':
        about(layout)
        return
    columns=layout.row().split(factor=.5)
    left=columns.column();right=columns.column()
    box=left.box()
    box.label(text='选中组显示')
    row=box.row(align=True)
    row.prop(settings,'selected_bounds_style',text='边界框')
    if settings.selected_bounds_style!='NONE':
        row.prop(settings,'selected_bounds_width')
        color_row(box,settings,'selected_bounds_color','线颜色')
    box=left.box()
    box.label(text='编辑行为')
    box.prop(settings,'edit_outliner')
    box=right.box()
    box.label(text='编辑组显示')
    row=box.row(align=True)
    row.prop(settings,'edit_bounds_style',text='边界框')
    if settings.edit_bounds_style!='NONE':
        row.prop(settings,'edit_bounds_width')
        color_row(box,settings,'edit_bounds_color','线颜色')
    box=right.box()
    box.label(text='当前场景：'+c.scene.name,icon='SCENE_DATA')
    outside.panel(box,c)
    work=instances.session(c)
    if work:
        if work.get('sg_session_preview_error'):box.label(text=work['sg_session_preview_error'],icon='ERROR')


class SG_MT_group(bpy.types.Menu):
    bl_idname='SG_MT_group'
    bl_label='SketchGroup'

    def draw(self,c):
        layout=self.layout
        work=instances.session(c)
        target=c.active_object
        if instances.is_instance(target) and target.select_get():
            layout.operator('sketchgroup.instance_enter',text='编辑')
            layout.operator('sketchgroup.instance_explode',text='解散').recursive=False
            layout.operator('sketchgroup.instance_explode',text='解散全部').recursive=True
            layout.operator('sketchgroup.instance_make_unique',text='独立化')
            layout.operator('sketchgroup.select_linked',text='选择关联组')
            layout.separator()
            layout.operator('sketchgroup.instance_duplicate',text='复制').linked=False
            layout.operator('sketchgroup.instance_duplicate',text='关联复制').linked=True
            layout.separator()
        layout.operator('sketchgroup.instance_create',text='创建群组')
        layout.operator('sketchgroup.instance_join',text='加入群组')
        if work:
            layout.separator()
            layout.operator('sketchgroup.instance_separate',text='分离选中项')
            layout.operator('sketchgroup.dissolve_current',text='解散当前组',icon='UNLINKED')
            layout.separator()
            layout.operator('sketchgroup.instance_finish',text='退出当前组',icon='CHECKMARK')


def menu(self,c):
    if c.mode=='OBJECT' and (c.selected_objects or instances.session(c)):
        self.layout.menu('SG_MT_group',text='SketchGroup')
        self.layout.separator()


def register():
    global _icons
    _icons=previews.new()
    for name,_,_ in SOCIAL_LINKS:
        try:_icons.load(name,str(Path(__file__).parent/'assets'/'icons'/(name+'.png')),'IMAGE')
        except (OSError,RuntimeError):pass
    bpy.utils.register_class(keymaps.SG_OT_restore_binding)
    bpy.utils.register_class(SG_MT_group)
    bpy.types.VIEW3D_MT_object_context_menu.prepend(menu)


def unregister():
    global _icons
    if _icons is not None:previews.remove(_icons);_icons=None
    bpy.types.VIEW3D_MT_object_context_menu.remove(menu)
    bpy.utils.unregister_class(SG_MT_group)
    bpy.utils.unregister_class(keymaps.SG_OT_restore_binding)
