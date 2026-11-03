"""Canonical scene parking for save/render, independent of edit entry routing."""
import bpy

PARKED='sg_session_parked'


def park(scene,work,*,restore_selection=True,restore_visibility=True):
    from . import instances as f
    if work.get(PARKED):return
    from . import coordinates
    target=work['sg_session_target']
    coordinates.unbind(work)
    coordinates.attach_target(work,restore_visibility=False)
    target.instance_collection=work
    target.instance_type='COLLECTION';target.hide_render=work['sg_session_render_hidden']
    if restore_selection:
        from . import selection_state
        selection_state.restore(work)
        selection_state.set_locked(target,work['sg_session_select_lock'])
    f.unlink_session(scene,work)
    from . import source_display
    source_display.park_container(scene,work)
    source_display.edit_visibility(scene,work,False)
    work[PARKED]=True
    if restore_visibility:coordinates.restore_target_visibility(target,work.get('sg_session_target_hidden_layers',{}))


def unpark(scene,work):
    from . import instances as f
    if not work.get(PARKED):return
    from . import coordinates
    target=work['sg_session_target']
    target.instance_type='NONE';target.instance_collection=None
    from . import source_display
    container=source_display.mount_container(scene,work)
    if work.name not in container.children:container.children.link(work)
    layer=scene.view_layers.get(work['sg_session_view_layer'])
    if layer:layer.update()
    source_display.edit_visibility(scene,work,True)
    coordinates.tidy(coordinates.bind(work))
    target.instance_type='NONE';target.hide_render=True
    from . import selection_state
    selection_state.reapply(work)
    selection_state.set_locked(target,True)
    if layer and target.name in layer.objects:target.hide_set(True,view_layer=layer)
    work[PARKED]=False
    if layer:layer.update()
    coordinates.detach_target(work)


def park_all():
    from .source_edit import sessions
    for scene,chain in sessions():
        layer=scene.view_layers.get(chain[0].get('sg_session_view_layer','')) or scene.view_layers[0]
        root=chain[-1]
        current=layer.objects.active
        root['sg_session_io_selection']={str(i):obj for i,obj in enumerate(layer.objects) if obj.select_get(view_layer=layer)}
        if current:
            root['sg_session_io_active']=current
            root['sg_session_io_mode']=current.mode
        collection=layer.active_layer_collection.collection
        if collection.is_embedded_data:
            if 'sg_session_io_layer' in root:del root['sg_session_io_layer']
        else:root['sg_session_io_layer']=collection
        for work in chain:park(scene,work)
    for scene,chain in sessions():
        for layer in scene.view_layers:layer.update()


def unpark_all():
    from .source_edit import sessions
    from . import instances as f
    for scene,chain in sessions():
        for work in reversed(chain):unpark(scene,work)
        root=chain[-1]
        if 'sg_session_io_selection' not in root:continue
        layer=scene.view_layers.get(chain[0].get('sg_session_view_layer','')) or scene.view_layers[0]
        layer.update()
        for obj in layer.objects:
            if obj.select_get(view_layer=layer):obj.select_set(False,view_layer=layer)
        for obj in root['sg_session_io_selection'].values():
            if obj and obj.name in layer.objects:obj.select_set(True,view_layer=layer)
        current=root.get('sg_session_io_active')
        if current and current.name in layer.objects:
            layer.objects.active=current
            mode=root.get('sg_session_io_mode','OBJECT')
            if current.mode!=mode:
                with bpy.context.temp_override(scene=scene,view_layer=layer):bpy.ops.object.mode_set(mode=mode)
        collection=root.get('sg_session_io_layer') or scene.collection
        if collection:
            restored=f.layer_for(layer.layer_collection,collection)
            if restored:layer.active_layer_collection=restored
        for key in list(root.keys()):
            if key.startswith('sg_session_io_'):del root[key]


