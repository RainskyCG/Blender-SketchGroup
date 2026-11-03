import bpy
from bpy.app.handlers import persistent

_enabled=False
_suspended=False
_focused={}
_fold_pending={}
COLOR_KEY='sg_session_original_color'


def restore_colors(work):
    original=work.get(COLOR_KEY)
    if original is not None:
        work.color_tag=original
        del work[COLOR_KEY]


def sync_colors():
    from .instances import ACTIVE,session_chain
    for scene in bpy.data.scenes:
        work=scene.get(ACTIVE)
        for index,level in enumerate(session_chain(work)):
            if COLOR_KEY not in level:level[COLOR_KEY]=level.color_tag
            color='COLOR_02' if index==0 else 'COLOR_05'
            if level.color_tag!=color:level.color_tag=color


def preference(c):
    addon=c.preferences.addons.get(__package__)
    return addon.preferences.edit_outliner if addon else True


def sync():
    if not _enabled or _suspended:return
    sync_colors()
    from .instances import ACTIVE
    from .source_edit import active
    live=set();use=preference(bpy.context)
    for window in bpy.context.window_manager.windows:
        work=window.scene.get(ACTIVE)
        editing=use and active(work)
        for area in window.screen.areas:
            if area.type!='OUTLINER':continue
            space=area.spaces.active;key=space.as_pointer();live.add(key)
            if editing and space.display_mode=='VIEW_LAYER' and not space.filter_text:
                signature=(window.scene.as_pointer(),window.view_layer.as_pointer(),work.as_pointer())
                pending=_fold_pending.get(key)
                if _focused.get(key)!=signature and (not pending or pending[2]!=signature):
                    _fold_pending[key]=(window,area,signature,0)
                    area.tag_redraw()
            else:
                _focused.pop(key,None);_fold_pending.pop(key,None)
    for key in set(_focused)-live:_focused.pop(key,None)
    if _fold_pending and not bpy.app.background and not bpy.app.timers.is_registered(flush_pending):
        bpy.app.timers.register(flush_pending,first_interval=.02)


def fold_pending():
    from .instances import ACTIVE,scope_objects
    for key,(window,area,signature,attempt) in tuple(_fold_pending.items()):
        try:
            space=area.spaces.active;work=window.scene.get(ACTIVE)
            if (not work or area.type!='OUTLINER' or space.as_pointer()!=key
                or space.display_mode!='VIEW_LAYER' or space.filter_text
                or signature!=(window.scene.as_pointer(),window.view_layer.as_pointer(),work.as_pointer())):
                _fold_pending.pop(key,None);continue
            layer=window.view_layer
            members=[obj for obj in scope_objects(work) if obj.name in layer.objects]
            target=next((obj for obj in members if obj==layer.objects.active),None)
            if target is None:target=min(members,key=lambda obj:obj.name.casefold(),default=None)
            region=next((r for r in area.regions if r.type=='WINDOW'),None)
            if target is None or region is None:
                _fold_pending.pop(key,None);continue
            active=layer.objects.active
            with bpy.context.temp_override(window=window,area=area,region=region):
                try:
                    # A closed item guarantees the next two toggles end fully closed.
                    # show_active then opens only the current member's ancestor path.
                    bpy.ops.outliner.show_one_level(open=False)
                    bpy.ops.outliner.expanded_toggle()
                    bpy.ops.outliner.expanded_toggle()
                    layer.objects.active=target
                    result=bpy.ops.outliner.show_active()
                finally:layer.objects.active=active
            if result=={'FINISHED'} or attempt>=4:
                _focused[key]=signature;_fold_pending.pop(key,None)
            else:_fold_pending[key]=(window,area,signature,attempt+1)
            area.tag_redraw()
        except (ReferenceError,RuntimeError) as ex:
            _focused[key]=signature
            _fold_pending.pop(key,None)
            print('SketchGroup 大纲折叠暂停：',ex)


def flush_pending():
    if not _enabled or _suspended:return None
    fold_pending()
    return .02 if _fold_pending else None


def tick():
    if not _enabled:return None
    sync()
    return .5 if _focused or _fold_pending else 1.0


def breadcrumb(self,c):
    from .instances import session,session_chain
    if not preference(c):return
    chain=list(reversed(session_chain(session(c))))
    if not chain:return
    row=self.layout.row(align=True)
    row.operator('sketchgroup.return_level',text='场景',emboss=False).depth=0
    for index,work in enumerate(chain,1):
        row.label(text='›')
        target=work.get('sg_session_target')
        item=row.row(align=True);item.enabled=index<len(chain)
        item.operator('sketchgroup.return_level',text=target.name if target else work.name,emboss=False).depth=index



@persistent
def suspend(*args):
    global _suspended
    _suspended=True
    _fold_pending.clear()
    for col in bpy.data.collections:
        if COLOR_KEY in col:
            col.color_tag=col[COLOR_KEY]


@persistent
def resume(*args):
    global _suspended
    _suspended=False
    sync()


def register():
    global _enabled,_suspended
    _enabled=True;_suspended=False
    bpy.types.OUTLINER_HT_header.append(breadcrumb)
    for handlers,callback in (
        (bpy.app.handlers.save_pre,suspend),(bpy.app.handlers.save_post,resume),(bpy.app.handlers.save_post_fail,resume),
        (bpy.app.handlers.load_pre,suspend),(bpy.app.handlers.load_post,resume),
        (bpy.app.handlers.undo_post,resume),(bpy.app.handlers.redo_post,resume),
    ):handlers.append(callback)
    if not bpy.app.background:bpy.app.timers.register(tick,first_interval=.1)


def unregister():
    global _enabled
    _enabled=False
    bpy.types.OUTLINER_HT_header.remove(breadcrumb)
    suspend()
    _focused.clear();_fold_pending.clear()
    if bpy.app.timers.is_registered(tick):bpy.app.timers.unregister(tick)
    if bpy.app.timers.is_registered(flush_pending):bpy.app.timers.unregister(flush_pending)
    for handlers,callback in (
        (bpy.app.handlers.save_pre,suspend),(bpy.app.handlers.save_post,resume),(bpy.app.handlers.save_post_fail,resume),
        (bpy.app.handlers.load_pre,suspend),(bpy.app.handlers.load_post,resume),
        (bpy.app.handlers.undo_post,resume),(bpy.app.handlers.redo_post,resume),
    ):
        if callback in handlers:handlers.remove(callback)
