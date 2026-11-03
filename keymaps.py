"""Edit Blender's persisted user bindings without changing addon defaults."""
import bpy
from bpy.props import IntProperty

EVENT_FIELDS=('type','value','any','shift','ctrl','alt','oskey','hyper','key_modifier','repeat','direction','active')


VARIANTS={
    'sketchgroup.instance_duplicate':('linked',),
}
GROUPS=(
    (
        ('创建与结构',(
            ('创建群组','instance_create',{}),
            ('加入群组','instance_join',{}),
            ('分离菜单','separate_menu',{}),
        )),
        ('复制',(
            ('独立复制','instance_duplicate',{'linked':False}),
            ('关联复制','instance_duplicate',{'linked':True}),
        )),
    ),
    (
        ('编辑',(
            ('编辑组','instance_enter',{}),
            ('进入 / 空选退出','tab_edit',{}),
            ('Esc 退出当前组','escape_exit',{}),
            ('退出当前组','instance_finish',{}),
        )),
        ('鼠标交互',(
            ('指针处进入组','double_click_enter',{}),
            ('指针在组外退出','click_exit',{}),
        )),
    ),
)


def defaults():
    from . import instances,interaction
    return (*instances.KEYS,*interaction.KEYS)


def same_action(item,default):
    return item.idname==default.idname and all(
        getattr(item.properties,key,None)==getattr(default.properties,key,None)
        for key in VARIANTS.get(default.idname,()))


def user_items(c,km,default):
    config=c.window_manager.keyconfigs.user
    if not config:return ()
    user_map=config.keymaps.find(km.name,space_type=km.space_type,region_type=km.region_type)
    if not user_map:return ()
    return tuple((user_map,item) for item in user_map.keymap_items if same_action(item,default))


def event_matches(item,event,value=None):
    if not item.active or item.type!=event.type:return False
    if item.value not in {'ANY',value or event.value}:return False
    if not item.any:
        for name in ('shift','ctrl','alt','oskey','hyper'):
            expected=getattr(item,name,0)
            if expected!=-1 and bool(expected)!=bool(getattr(event,name,False)):return False
    return True


def accepts(c,idname,event,*,value=None):
    for km,default in defaults():
        if default.idname!=idname:continue
        items=user_items(c,km,default)
        # Background Blender has no merged window keymaps. GUI input always
        # uses the user map, including deliberately disabled or removed keys.
        if not items and bpy.app.background:items=((km,default),)
        if any(event_matches(item,event,value) for _,item in items):return True
    return False


def default_for(km,item):
    return next((default for source,default in defaults()
        if source.name==km.name and source.space_type==km.space_type and same_action(item,default)),None)


class SG_OT_restore_binding(bpy.types.Operator):
    bl_idname='sketchgroup.restore_binding'
    bl_label='恢复此快捷键'
    bl_options={'INTERNAL'}
    item_id:IntProperty()
    @classmethod
    def poll(cls,c):return getattr(c,'keymap',None) is not None
    def execute(self,c):
        item=c.keymap.keymap_items.from_id(self.item_id)
        default=default_for(c.keymap,item) if item else None
        if not default:return {'CANCELLED'}
        if item.is_user_defined:
            for field in EVENT_FIELDS:setattr(item,field,getattr(default,field))
        else:c.keymap.restore_item_to_default(item)
        c.preferences.is_dirty=True
        return {'FINISHED'}


def draw_item(layout,km,item,label):
    col=layout.column(align=True)
    col.context_pointer_set('keymap',km)
    row=col.row(align=True)
    row.prop(item,'show_expanded',text='',emboss=False)
    row.prop(item,'active',text='',emboss=False)
    split=row.split(factor=.47,align=True)
    split.label(text=label)
    keys=split.row(align=True)
    keys.prop(item,'type',text='',full_event=True)
    restore=keys.row(align=True)
    default=default_for(km,item)
    restore.enabled=bool(default and any(getattr(item,f)!=getattr(default,f) for f in EVENT_FIELDS))
    restore.operator('sketchgroup.restore_binding',text='',icon='BACK').item_id=item.id
    if item.show_expanded:
        box=col.box()
        row=box.row(align=True)
        row.prop(item,'map_type',text='')
        if item.idname!='sketchgroup.click_exit':row.prop(item,'value',text='')
        row=box.row(align=True)
        row.prop(item,'any',text='任意',toggle=True)
        modifiers=row.row(align=True);modifiers.enabled=not item.any
        for name,label in (('shift','Shift'),('ctrl','Ctrl'),('alt','Alt'),('oskey','Win')):
            modifiers.prop(item,name+'_ui',text=label,toggle=True)
        row=box.row(align=True)
        row.prop(item,'key_modifier',text='附加键',event=True)
        if item.map_type=='KEYBOARD':row.prop(item,'repeat',text='重复')
        if item.value=='CLICK_DRAG':box.prop(item,'direction',text='方向')


def draw(layout,c):
    columns=layout.row().split(factor=.5)
    bindings=defaults()
    for groups in GROUPS:
        col=columns.column()
        for title,actions in groups:
            box=col.box();box.label(text=title)
            for label,operator,properties in actions:
                default=next(((km,item) for km,item in bindings
                    if item.idname=='sketchgroup.'+operator and all(
                        getattr(item.properties,key,None)==value for key,value in properties.items())),None)
                items=user_items(c,*default) if default else ()
                if not items:
                    box.label(text=label+'：未绑定')
                for km,item in items:draw_item(box,km,item,label)
