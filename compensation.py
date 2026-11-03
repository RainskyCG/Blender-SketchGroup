"""Reverse-transform shared instances without redirecting their source collections."""
from .coordinates import pack,unpack
from .explode import close_matrix,simple_matrix

STATE='sg_direct_compensation'
_objects={}


def restore(obj):
    record=obj.get(STATE)
    if not record:return
    before=unpack(record['before']);after=unpack(record['after']);change=unpack(record['change'])
    obj.matrix_basis=before if close_matrix(obj.matrix_basis,after) else obj.matrix_basis@change.inverted()
    for entry in record['children'].values():
        child=entry.get('object')
        if child and child.parent==obj:
            old=unpack(entry['before']);new=unpack(entry['after'])
            child.matrix_parent_inverse=old if close_matrix(child.matrix_parent_inverse,new) else change@child.matrix_parent_inverse
    del obj[STATE]


def clear(force=False):
    import bpy
    for obj in tuple(bpy.data.objects) if force else tuple(_objects.values()):
        try:restore(obj)
        except ReferenceError:pass
    _objects.clear()


def apply(obj,work,change,inverse,child_objects):
    """False requests the existing affine bridge for a non-TRS/animated instance."""
    previous=obj.get(STATE)
    if previous:
        if previous.get('work')==work and close_matrix(unpack(previous['change']),change):
            _objects[obj.as_pointer()]=obj
            return True
        restore(obj)
    before=obj.matrix_basis.copy();after=before@change
    if obj.animation_data or not close_matrix(after,simple_matrix(after)):return False
    children={str(i):{'object':child,'before':pack(child.matrix_parent_inverse),
                      'after':pack(inverse@child.matrix_parent_inverse)}
              for i,child in enumerate(child_objects)}
    obj[STATE]={'work':work,'before':pack(before),'after':pack(after),'change':pack(change),'children':children}
    _objects[obj.as_pointer()]=obj
    obj.matrix_basis=after
    for entry in children.values():entry['object'].matrix_parent_inverse=unpack(entry['after'])
    return True


def prune(wanted):
    for key,obj in tuple(_objects.items()):
        if key in wanted:continue
        try:restore(obj)
        except ReferenceError:pass
        del _objects[key]
