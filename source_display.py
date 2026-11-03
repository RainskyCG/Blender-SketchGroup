"""Preserve ordinary scene occurrences while the original collection is edited."""
import bpy
from mathutils import Matrix

LINKS='sg_session_scene_links'
DISPLAY='sg_source_display'
WRAPPER='sg_source_edit_container'
FIELDS=('exclude','hide_viewport','holdout','indirect_only')
_displays=[]
_memberships={}


def parent_of(record):
    scene=record.get('scene')
    return scene.collection if scene else record.get('parent')


def layer_at(scene,name,path):
    layer=scene.view_layers.get(name)
    if not layer:return None
    current=layer.layer_collection
    for col in path:
        current=next((child for child in current.children if child.collection==col),None)
        if current is None:return None
    return current


def capture(collections):
    records=[]
    scene_roots={scene.collection:scene for scene in bpy.data.scenes}
    reachable=set(scene_roots)
    for scene in bpy.data.scenes:reachable.update(scene.collection.children_recursive)
    for parent in reachable-set(collections):
        for child in parent.children:
            if child not in collections:continue
            if parent.library or parent.override_library:raise ValueError('源集合的场景父级不可写')
            record={'source':child,'layers':{}}
            record['scene' if parent.is_embedded_data else 'parent']=scene_roots[parent] if parent.is_embedded_data else parent
            records.append(record)
    if not records:return {}
    views={}
    for scene in bpy.data.scenes:
        for layer in scene.view_layers:
            roots=set();allowed=set();hidden=set()
            def remember(node,path,states,enabled):
                col=node.collection
                states[str(len(states))]={'scene':scene,'layer':layer.name,'path':{str(i):item for i,item in enumerate(path)},**{field:getattr(node,field) for field in FIELDS}}
                enabled=enabled and not node.exclude and not node.hide_viewport and not col.hide_viewport
                if enabled:
                    allowed.add(col)
                    hidden.update(obj for obj in col.objects if obj.name in layer.objects and obj.hide_get(view_layer=layer))
                for child in node.children:remember(child,path+[child.collection],states,enabled)
            def visit(node,path,enabled=True):
                enabled=enabled and not node.exclude and not node.hide_viewport and not node.collection.hide_viewport
                for child in node.children:
                    child_path=path+[child.collection]
                    for record in records:
                        if node.collection==parent_of(record) and child.collection==record['source']:
                            roots.add(child.collection)
                            remember(child,child_path,record['layers'],enabled)
                    visit(child,child_path,enabled)
            visit(layer.layer_collection,[])
            if roots:
                views[str(len(views))]={'scene':scene,'layer':layer.name,
                    'roots':{str(i):col for i,col in enumerate(roots)},
                    'allowed':{str(i):col for i,col in enumerate(allowed)},
                    'known':{str(i):col for i,col in enumerate(collections)},
                    'hidden':{str(i):obj for i,obj in enumerate(hidden)}}
    return {'links':{str(i):record for i,record in enumerate(records)},'views':views}


def apply_layers(record):
    for state in record['layers'].values():
        node=layer_at(state['scene'],state['layer'],list(state['path'].values()))
        if node:
            for field in FIELDS:setattr(node,field,bool(state[field]))


def mount_container(scene,work):
    if not work.get(LINKS):return work.get('sg_session_parent') or scene.collection
    container=work.get('sg_session_container')
    if not container or not container.get(WRAPPER):
        container=bpy.data.collections.new('SketchGroup');container[WRAPPER]=True
        work['sg_session_container']=container
    parent=work.get('sg_session_parent') or scene.collection
    if container.name not in parent.children:parent.children.link(container)
    from .instances import layer_for
    for layer in scene.view_layers:
        node=layer_for(layer.layer_collection,container)
        if node:node.exclude=not work.get('sg_session_edit_visible_layers',{}).get(layer.name,True)
    return container


def park_container(scene,work):
    container=work.get('sg_session_container')
    if container and container.get(WRAPPER):
        parent=work.get('sg_session_parent') or scene.collection
        if container.name in parent.children:parent.children.unlink(container)


def edit_visibility(scene,work,editing):
    layer=scene.view_layers.get(work.get('sg_session_view_layer',''))
    if not layer:return
    for view in work.get(LINKS,{}).get('views',{}).values():
        if view['scene']==scene and view['layer']==layer.name:
            for obj in view['hidden'].values():
                if obj and not obj.get('sg_coordinate_anchor') and obj.name in layer.objects:
                    obj.hide_set(not editing,view_layer=layer)


def finish(work):
    container=work.get('sg_session_container')
    if container and container.get(WRAPPER):bpy.data.collections.remove(container)


def clear(force=False,*,keep=()):
    displays=[col for col in bpy.data.collections if col.get(DISPLAY)] if force else list(_displays)
    _displays.clear();_memberships.clear()
    restored=set()
    for proxy in displays:
        work=proxy.get('sg_source_work')
        if work and work not in restored:
            records=work.get(LINKS,{}).get('links',{}).values()
            for record in records:
                parent=parent_of(record);source=record['source']
                if parent and source and source.name not in parent.children:parent.children.link(source)
            for record in records:apply_layers(record)
            restored.add(work)
        definition=proxy.get('sg_source_definition');owned=list(proxy.objects)
        if work in keep:
            view=work[LINKS]['views'][proxy['sg_source_view']]
            if proxy.name in view['scene'].collection.children:view['scene'].collection.children.unlink(proxy)
            if definition:
                for obj in tuple(definition.objects):definition.objects.unlink(obj)
            _displays.append(proxy)
            continue
        bpy.data.collections.remove(proxy)
        if definition:bpy.data.collections.remove(definition)
        if owned:bpy.data.batch_remove(ids=owned)


def update():
    for proxy in _displays:
        work=proxy['sg_source_work'];target=work.get('sg_session_target')
        if not target:continue
        carrier=proxy['sg_source_carrier']
        from . import coordinates
        matrix=coordinates.frame(work).inverted()
        if carrier.matrix_parent_inverse!=matrix:carrier.matrix_parent_inverse=matrix


def members(view):
    allowed=set(view['allowed'].values());known=set(view['known'].values())
    hidden=set(view['hidden'].values());result=set();visited=set()
    def visit(col,parent_visible=False):
        if col in visited:return
        visited.add(col)
        _memberships[col.as_pointer()]=tuple(obj.as_pointer() for obj in col.objects)
        enabled=(col in allowed or (col not in known and parent_visible)) and not col.hide_viewport
        if enabled:result.update(obj for obj in col.objects if obj not in hidden and not obj.get('sg_coordinate_anchor'))
        for child in col.children:visit(child,enabled)
    for root in view['roots'].values():visit(root)
    return result


def structure_changed(col):
    previous=_memberships.get(col.as_pointer())
    return previous is not None and previous!=tuple(obj.as_pointer() for obj in col.objects)


def refresh(chain):
    _memberships.clear()
    existing={(proxy['sg_source_work'],proxy['sg_source_view']):proxy for proxy in _displays}
    for work in chain:
        state=work.get(LINKS)
        if not state:continue
        for key,view in state['views'].items():
            proxy=existing.get((work,key))
            if proxy is None:
                proxy=bpy.data.collections.new(work.name+' · 显示');proxy[DISPLAY]=True
                proxy['sg_source_work']=work;proxy['sg_source_view']=key
                _displays.append(proxy)
                definition=bpy.data.collections.new('SG · 源显示引用');definition['sg_source_display_definition']=True
                proxy['sg_source_definition']=definition
                root=bpy.data.objects.new('SG · 源显示坐标',None);proxy.objects.link(root)
                carrier=bpy.data.objects.new('SG · 源显示',None);proxy.objects.link(carrier)
                root[DISPLAY]=True;carrier[DISPLAY]=True
                proxy['sg_source_carrier']=carrier
                carrier.parent=root;carrier.matrix_basis=Matrix.Identity(4)
                carrier.instance_type='COLLECTION';carrier.instance_collection=definition
                carrier.hide_select=True;carrier.show_instancer_for_viewport=False
                carrier.empty_display_type='IMAGE';carrier.show_empty_image_orthographic=False;carrier.show_empty_image_perspective=False
                from . import markers
                markers.hide_helper(root)
            scene=view['scene']
            if proxy.name not in scene.collection.children:
                scene.collection.children.link(proxy)
                for layer in scene.view_layers:layer.layer_collection.children[proxy.name].exclude=layer.name!=view['layer']
            definition=proxy['sg_source_definition']
            wanted=members(view);current=set(definition.objects)
            for obj in current-wanted:definition.objects.unlink(obj)
            for obj in wanted-current:definition.objects.link(obj)
        for record in state['links'].values():
            parent=parent_of(record);source=record['source']
            if parent and source and source.name in parent.children:parent.children.unlink(source)
    update()


def outline_roots():
    return [proxy['sg_source_carrier'] for proxy in _displays]
