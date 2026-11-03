"""Persistent selection locks for source editing."""
import uuid

LOCK_TOKEN='sg_temp_select_owner'
LOCK_PREVIOUS='sg_temp_select_previous'


def set_locked(obj,value):
    if obj.hide_select!=value:obj.hide_select=value


def lock_object(obj,work):
    token=work.get('sg_session_token')
    if not token:
        token=uuid.uuid4().hex;work['sg_session_token']=token
    if not obj.get(LOCK_TOKEN):
        obj[LOCK_PREVIOUS]=bool(obj.hide_select)
        obj[LOCK_TOKEN]=token
    set_locked(obj,True)


def unlock_object(obj,previous=False):
    set_locked(obj,bool(obj.get(LOCK_PREVIOUS,previous)))
    for key in (LOCK_TOKEN,LOCK_PREVIOUS):
        if key in obj:del obj[key]


def lock_scope(c,work,candidates,editable,target,locks):
    """Lock complete outside branches once; retain object locks on mixed paths."""
    outside=set(candidates)-set(editable)
    outside.add(target)
    loose=set();visited=set()
    def visit(col):
        if col==work or col in visited:return
        visited.add(col)
        members=set(col.all_objects)
        if not members.intersection(outside):return
        if members<=outside and not col.is_embedded_data and not col.library and not col.override_library:
            if not col.hide_select:
                locks.append(col);lock_object(col,work)
            return
        loose.update(obj for obj in col.objects if obj in outside)
        for child in col.children:visit(child)
    visit(c.scene.collection)
    for obj in loose:
        if obj!=target and not obj.hide_select and not obj.library and not obj.override_library:
            locks.append(obj);lock_object(obj,work)


def restore(work,clear=False):
    for value in work.get('sg_session_locks',{}).values():
        obj=value.get('object')
        if not obj or obj.library:continue
        previous=bool(obj.get(LOCK_PREVIOUS,value.get('locked',False)))
        if clear:unlock_object(obj,previous)
        else:set_locked(obj,previous)


def reapply(work):
    for value in work.get('sg_session_locks',{}).values():
        obj=value.get('object')
        if obj and not obj.library:set_locked(obj,True)
