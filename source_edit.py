"""Direct definition editing. Source IDs survive entry and exit unchanged."""
import bpy

from .session_io import PARKED,park,unpark,park_all,unpark_all


def active(work):
    return isinstance(work,bpy.types.Collection) and bool(work.get('sg_instance_session') and work.get('sg_session_baked'))


def sessions():
    from . import instances as f
    return [(scene,f.session_chain(scene.get(f.ACTIVE))) for scene in bpy.data.scenes
            if active(scene.get(f.ACTIVE))]


def check_source(c,target,references):
    from . import instances as f,safety,preview
    f.check_instance(target,allow_animation=True)
    source=target.get(preview.SOURCE) or target.instance_collection
    if source.get(f.SESSION):raise ValueError('这个源集合已在编辑路径中，不能重复进入')
    cols,objects=f.validate_source(source,deep=False)
    safety.check_incoming(objects,cols)
    peers=[obj for obj in references[0].get(source,()) if obj!=target]
    if any(obj.library or obj.override_library for obj in peers):
        raise ValueError('存在不可写的关联实例，无法建立原位补偿')
    return source,objects


def enter(c):
    from . import instances as f,preview,outliner,viewport,source_display,coordinates
    parent=f.session(c);target=c.active_object
    if any(scene!=c.scene for scene,chain in sessions()):
        raise ValueError('请先退出另一场景的组编辑，再在此场景进入')
    c.view_layer.update()
    retained=f.session_chain(parent)
    preview.clear(keep=retained)
    try:
        references=preview.reference_snapshot()
        source,objects=check_source(c,target,references)
        scene_links=source_display.capture(f.collection_tree(source))
    except Exception:
        preview.refresh(c,True)
        raise
    original_selection=tuple(c.selected_objects);original_active=c.view_layer.objects.active
    original_layer=c.view_layer.active_layer_collection.collection
    candidates=tuple(f.scope_objects(parent) if parent else c.view_layer.objects)
    states={obj:(obj.parent,obj.matrix_parent_inverse.copy(),obj.matrix_basis.copy()) for obj in objects}
    state=(target.instance_type,target.hide_get(),target.hide_select,target.hide_render)
    locks=[];linked=False;helpers=set();ledger=[]
    try:
        if scene_links:
            source[source_display.LINKS]=scene_links
            source['sg_session_edit_visible_layers']={layer.name:target.visible_get(view_layer=layer) for layer in c.scene.view_layers}
        if parent:source['sg_session_parent']=parent
        container=source_display.mount_container(c.scene,source)
        container.children.link(source);linked=True
        source[f.SESSION]=True;source[PARKED]=False
        source['sg_session_target']=target;source['sg_session_source']=source
        coordinates.store_target(source,c.scene)
        if parent and not scene_links:source['sg_session_container']=parent
        # Scene master collections are embedded IDs and cannot be ID properties.
        # A missing layer reference means the current scene's root collection.
        if not original_layer.is_embedded_data:source['sg_session_layer']=original_layer
        source['sg_session_hidden']=state[1];source['sg_session_select_lock']=state[2]
        source['sg_session_render_hidden']=state[3];source['sg_session_view_layer']=c.view_layer.name
        from . import selection_state
        selection_state.lock_scope(c,source,candidates,objects,target,locks)
        source['sg_session_locks']={str(i):{'object':obj,'locked':False} for i,obj in enumerate(locks)}
        # Retain the original instance ID, but display the source objects directly.
        target.instance_type='NONE';target.instance_collection=None
        references[1].pop(target.as_pointer(),None)
        helpers=coordinates.bind(source,ledger)
        target.instance_type='NONE';target.hide_set(True);target.hide_render=True;f.lock_object(target,source)
        c.scene[f.ACTIVE]=source
        preview.refresh(c,True,evaluate=False,references=references)
        if source.get('sg_session_preview_error'):raise ValueError(source['sg_session_preview_error'])
        preview.evaluate(c)
        source_display.edit_visibility(c.scene,source,True)
        f.set_layer(c,source)
        f.include_local_view(c,list(objects));f.choose(c,[],update=False)
        outliner.sync();viewport.sync()
        coordinates.detach_target(source)
        coordinates.tidy(helpers)
        return source
    except Exception:
        preview.clear(keep=retained)
        for obj,(old_parent,inverse,basis) in states.items():
            obj.parent=old_parent;obj.matrix_parent_inverse=inverse;obj.matrix_basis=basis
        if ledger:bpy.data.batch_remove(ids=ledger)
        if source.get(coordinates.BAKED):coordinates.attach_target(source)
        for obj in locks:f.unlock_object(obj)
        f.unlock_object(target,state[2]);target.instance_collection=source;target.instance_type=state[0];target.hide_render=state[3];target.hide_set(state[1])
        if linked and source.name in container.children:container.children.unlink(source)
        source_display.edit_visibility(c.scene,source,False)
        source_display.finish(source)
        f.clear_session(c,source)
        f.set_layer(c,original_layer);f.choose(c,original_selection);c.view_layer.objects.active=original_active
        preview.refresh(c,True)
        raise


def leave(c):
    from . import instances as f,preview,outliner,viewport,coordinates
    work=f.session(c)
    if not active(work):raise ValueError('当前不是源集合编辑')
    f.validate_finish(work)
    c.view_layer.update();preview.clear(keep=f.session_chain(work.get('sg_session_parent')))
    target=work['sg_session_target'];layer=work.get('sg_session_layer') or c.scene.collection
    hidden=dict(work.get('sg_session_target_hidden_layers',{}))
    park(c.scene,work,restore_selection=False,restore_visibility=False)
    from . import selection_state
    selection_state.restore(work,clear=True)
    f.unlock_object(target,work['sg_session_select_lock'])
    outliner.restore_colors(work)
    from . import source_display
    source_display.finish(work)
    f.clear_session(c,work)
    preview.refresh(c,True,evaluate=False)
    preview.evaluate(c)
    coordinates.restore_target_visibility(target,hidden,evaluated_layer=c.view_layer)
    f.set_layer(c,layer);f.choose(c,[target],update=False)
    outliner.sync();viewport.sync()
    return target
