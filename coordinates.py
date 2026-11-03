"""Direct source transforms with affine carriers only when required."""
import bpy
from mathutils import Matrix

BAKED='sg_session_baked'
FRAME='sg_session_frame'


def pack(matrix):return [value for row in matrix for value in row]


def unpack(values):return Matrix([values[i:i+4] for i in range(0,16,4)])


def frame(work):return unpack(work[FRAME])


def matrices(objects):
    result={}
    def world(obj):
        if obj not in result:
            result[obj]=(world(obj.parent)@obj.matrix_parent_inverse@obj.matrix_basis
                         if obj.parent else obj.matrix_basis.copy())
        return result[obj]
    for obj in objects:world(obj)
    return result


def roots(work):
    from .instances import scope_objects
    from .safety import object_refs
    objects=scope_objects(work)
    helpers={obj for obj in objects if obj.get('sg_affine_carrier')}
    if not helpers:return [obj for obj in objects if obj.parent is None],set()
    referenced={value for obj in objects for _,_,value,_,_ in object_refs(obj) if value in helpers}
    removable=helpers-referenced
    def parent(obj):
        value=obj.parent
        while value in removable:value=value.parent
        return value
    return [obj for obj in objects-removable if parent(obj) is None],removable


def tidy(helpers):
    pending=set(helpers)
    while pending:
        empty=[obj for obj in pending if not obj.children]
        if not empty:break
        for obj in empty:
            pending.remove(obj);bpy.data.objects.remove(obj,do_unlink=True)


def bind(work,ledger=None):
    items,helpers=roots(work);world=matrices(items);transform=frame(work)
    desired={obj:transform@world[obj] for obj in items}
    write(work,desired,ledger)
    work['sg_session_coordinate_roots']={str(i):{'object':obj,'before':pack(world[obj]),'after':pack(desired[obj])}
                                        for i,obj in enumerate(items)}
    return helpers


def write(work,worlds,ledger=None):
    from .explode import close_matrix,simple_matrix
    from . import markers
    helpers=[obj for obj in work.objects if obj not in worlds and obj.get('sg_coordinate_anchor')
             and obj.get('sg_affine_carrier') and obj.parent is None and obj.matrix_basis==Matrix.Identity(4)]
    carrier=helpers[0] if helpers else None
    for obj,matrix in worlds.items():
        if close_matrix(matrix,simple_matrix(matrix)):
            obj.parent=None;obj.matrix_parent_inverse=Matrix.Identity(4);obj.matrix_basis=matrix
        else:
            if carrier is None:
                carrier=bpy.data.objects.new('SG · 仿射补偿',None)
                carrier['sg_coordinate_anchor']=True;carrier['sg_affine_carrier']=True
                work.objects.link(carrier);markers.hide_helper(carrier)
                if ledger is not None:ledger.append(carrier)
            obj.parent=carrier;obj.matrix_parent_inverse=matrix;obj.matrix_basis=Matrix.Identity(4)


def unbind(work):
    from .explode import close_matrix
    items,helpers=roots(work);inverse=frame(work).inverted();world=matrices(items)
    previous={entry.get('object'):entry for entry in work.get('sg_session_coordinate_roots',{}).values()}
    result={}
    for obj in items:
        entry=previous.get(obj)
        result[obj]=(unpack(entry['before']) if entry and close_matrix(world[obj],unpack(entry['after'])) else inverse@world[obj])
    write(work,result);tidy(helpers)


def target_collections(work):
    return [record['scene'].collection if record.get('scene') else record.get('collection')
            for record in work.get('sg_session_target_links',{}).values()]


def store_target(work,scene):
    target=work['sg_session_target'];records={}
    roots={s.collection:s for s in bpy.data.scenes}
    for index,col in enumerate(target.users_collection):
        records[str(index)]={'scene':roots[col]} if col.is_embedded_data else {'collection':col}
    work['sg_session_target_links']=records
    work['sg_session_target_hidden_layers']={layer.name:target.hide_get(view_layer=layer)
                                           for layer in scene.view_layers if target.name in layer.objects}
    work['sg_session_fake_user']=target.use_fake_user
    work[FRAME]=pack(target.matrix_world@Matrix.Translation(-work.instance_offset))
    work[BAKED]=True


def detach_target(work):
    target=work['sg_session_target'];target.use_fake_user=True
    for col in tuple(target.users_collection):col.objects.unlink(target)


def attach_target(work,restore_visibility=True):
    target=work['sg_session_target']
    for col in target_collections(work):
        if col and target.name not in col.objects:col.objects.link(target)
    target.use_fake_user=work.get('sg_session_fake_user',False)
    if restore_visibility:restore_target_visibility(target,work.get('sg_session_target_hidden_layers',{}))


def restore_target_visibility(target,hidden,evaluated_layer=None):
    for scene in target.users_scene:
        for layer in scene.view_layers:
            if layer!=evaluated_layer:layer.update()
            if target.name in layer.objects:
                target.hide_set(hidden.get(layer.name,False),view_layer=layer)
