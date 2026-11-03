import bpy
from . import instances,outliner,preview,viewport,markers,outside,silhouette,ui,interaction,origins
from bpy.props import BoolProperty,EnumProperty,FloatProperty,FloatVectorProperty

RUNTIME_VERSION='1.0.0'
def settings_changed(self,c):
    outliner.sync()
    for window in c.window_manager.windows:
        for area in window.screen.areas:
            if area.type in {'VIEW_3D','OUTLINER'}:area.tag_redraw()


class SG_Preferences(bpy.types.AddonPreferences):
    bl_idname=__package__
    preferences_tab: EnumProperty(name='设置',items=(('GENERAL','常规','显示与编辑设置'),('KEYMAP','按键映射','修改、停用或恢复快捷键'),('ABOUT','关于','作者主页与作品')),default='GENERAL',options={'SKIP_SAVE'})
    # Blender's preferences expose saved values through RNA, not raw ID properties.
    selected_bounds_style: EnumProperty(name='选中组边界框',description='选中组的三维边界框样式；不影响编辑范围框',items=(('NONE','无','不显示选中组边界框'),('SOLID','实线','显示连续三维边界框'),('DASHED','虚线','显示静态三维虚线边界框')),default='SOLID',update=outside.mask_color_changed)
    selected_bounds_width: FloatProperty(name='线宽',description='边界框线宽，单位为屏幕像素',default=1,min=.5,max=6,soft_max=3,update=outside.mask_color_changed)
    selected_bounds_color: FloatVectorProperty(name='颜色',description='选中组边界框颜色和不透明度',subtype='COLOR_GAMMA',size=4,min=0,max=1,default=(1,.627,.157,.48),update=outside.mask_color_changed)
    edit_bounds_style: EnumProperty(name='编辑组边界框',description='编辑范围框的显示样式；隐藏后仍可点击组框外退出',items=(('NONE','无','不显示编辑范围框'),('SOLID','实线','显示连续三维边界框'),('DASHED','虚线','显示静态三维虚线边界框')),default='DASHED',update=outside.mask_color_changed)
    edit_bounds_width: FloatProperty(name='线宽',description='编辑范围框线宽，单位为屏幕像素',default=1,min=.5,max=6,soft_max=3,update=outside.mask_color_changed)
    edit_bounds_color: FloatVectorProperty(name='颜色',description='编辑范围框颜色和不透明度',subtype='COLOR_GAMMA',size=4,min=0,max=1,default=(.66,.69,.72,.52),update=outside.mask_color_changed)
    edit_outliner: BoolProperty(name='编辑时自动折叠大纲',description='进入或切换层级时收起其他分支并定位当前组；保留搜索和过滤设置，有搜索内容时不自动折叠',default=True,update=settings_changed)
    def draw(self,c):
        ui.preferences(self.layout,c,self)


def register():
    bpy.utils.register_class(SG_Preferences)
    instances.register()
    interaction.register()
    origins.register()
    outliner.register()
    viewport.register()
    preview.register()
    outside.register()
    silhouette.register()
    ui.register()


def unregister():
    origins.unregister()
    ui.unregister()
    interaction.unregister()
    silhouette.unregister()
    outside.unregister()
    preview.unregister()
    outliner.unregister()
    viewport.unregister()
    instances.unregister()
    bpy.utils.unregister_class(SG_Preferences)
