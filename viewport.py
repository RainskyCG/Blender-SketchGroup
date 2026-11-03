import bpy
from bpy.app.handlers import persistent

_states={}
_enabled=False
_suspended=False


def restore(key):
    entry=_states.pop(key,None)
    if not entry:return
    space,original=entry
    try:
        if not space.overlay.show_relationship_lines:
            space.overlay.show_relationship_lines=original
    except (ReferenceError,RuntimeError):pass


def sync():
    if not _enabled or _suspended:return
    from .instances import ACTIVE
    from .source_edit import active
    live=set()
    for window in bpy.context.window_manager.windows:
        work=window.scene.get(ACTIVE)
        editing=active(work)
        for area in window.screen.areas:
            if area.type!='VIEW_3D':continue
            space=area.spaces.active;key=space.as_pointer();live.add(key)
            if editing and key not in _states:
                _states[key]=(space,space.overlay.show_relationship_lines)
                space.overlay.show_relationship_lines=False
                area.tag_redraw()
            elif not editing and key in _states:
                restore(key);area.tag_redraw()
    for key in set(_states)-live:restore(key)


def tick():
    if not _enabled:return None
    sync()
    return .5 if _states else 1.0


@persistent
def suspend(*args):
    global _suspended
    _suspended=True
    for key in list(_states):restore(key)


@persistent
def resume(*args):
    global _suspended
    _suspended=False
    sync()


def handlers():
    return (
        (bpy.app.handlers.save_pre,suspend),(bpy.app.handlers.save_post,resume),
        (bpy.app.handlers.save_post_fail,resume),(bpy.app.handlers.load_pre,suspend),
        (bpy.app.handlers.load_post,resume),(bpy.app.handlers.undo_pre,suspend),
        (bpy.app.handlers.undo_post,resume),(bpy.app.handlers.redo_pre,suspend),
        (bpy.app.handlers.redo_post,resume),
    )


def register():
    global _enabled,_suspended
    _enabled=True;_suspended=False
    for items,callback in handlers():items.append(callback)
    if not bpy.app.background:bpy.app.timers.register(tick,first_interval=.1)


def unregister():
    global _enabled
    _enabled=False
    suspend()
    if bpy.app.timers.is_registered(tick):bpy.app.timers.unregister(tick)
    for items,callback in handlers():
        if callback in items:items.remove(callback)
