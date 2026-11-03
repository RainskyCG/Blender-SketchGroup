import bpy
from functools import wraps
from mathutils import Matrix
from bpy.app.handlers import persistent

_enabled=False
_busy=False
_suspended=False
_owned=[]
_records={}
_redirected={}
_dirty=True
_signature=None
_outline_roots=[]
_instance_links={}
_collection_links={}
SOURCE='sg_preview_original'
OWNED='sg_preview_owned'


def restore_after(operation):
    @wraps(operation)
    def wrapped(self,c,*args,**kwargs):
        try:return operation(self,c,*args,**kwargs)
        finally:
            from . import source_edit,instances
            if source_edit.active(instances.session(c)):
                c.view_layer.update();refresh(c,True)
    return wrapped


def mark():
    global _dirty
    _dirty=True


def clear(force=False,*,keep=()):
    global _busy,_signature
    was_busy=_busy;_busy=True
    try:
        from . import source_display,compensation
        compensation.clear(force)
        keep=set(keep) if not force else set()
        source_display.clear(force,keep=keep)
        objects=list(bpy.data.objects) if force else list(_redirected.values())
        for obj in objects:
            try:
                source=obj.get(SOURCE)
                if isinstance(source,bpy.types.Collection):
                    obj.instance_collection=source
                    del obj[SOURCE]
            except ReferenceError:pass
        retained={key:record for key,record in _records.items() if record[0] in keep}
        retained_ids={item for record in retained.values() for item in record[1:]}
        owned=[item for pool in (bpy.data.objects,bpy.data.collections,bpy.data.textures,bpy.data.meshes,bpy.data.curves,bpy.data.cameras,bpy.data.lights,bpy.data.lattices) for item in pool if item.get(OWNED)] if force else [item for item in _owned if item not in retained_ids]
        if owned:bpy.data.batch_remove(ids=owned)
        _owned[:]=retained_ids;_records.clear();_records.update(retained)
        _redirected.clear();_outline_roots.clear();_instance_links.clear();_collection_links.clear()
        _signature=None;mark()
    finally:_busy=was_busy


def bridge(work):
    ledger=[]
    try:
        proxy=bpy.data.collections.new('SG · 关联显示');ledger.append(proxy)
        root=bpy.data.objects.new('SG · 预览坐标',None);ledger.append(root)
        carrier=bpy.data.objects.new('SG · 编辑引用',None);ledger.append(carrier)
        for obj in (root,carrier):
            proxy.objects.link(obj);obj.hide_select=True
        from . import markers
        markers.hide_helper(root)
        carrier.show_instancer_for_viewport=False
        carrier.parent=root;carrier.matrix_basis=Matrix.Identity(4)
        carrier.instance_type='COLLECTION';carrier.instance_collection=work
        for item in ledger:item[OWNED]=True
        _owned.extend(ledger)
        return work,proxy,root,carrier
    except Exception:
        if ledger:bpy.data.batch_remove(ids=ledger)
        raise


def rebuild_outline_roots(c):
    from . import source_edit,instances as f
    if not source_edit.active(f.session(c)):
        _outline_roots[:]=_redirected.values();return
    memo={}
    def contains_redirect(collection,path=frozenset()):
        key=collection.as_pointer()
        if key in memo:return memo[key]
        if key in path:return False
        if collection.get(OWNED) or collection.get('sg_session_baked'):memo[key]=True;return True
        children=list(collection.children)+[obj.instance_collection for obj in collection.objects if obj.instance_type=='COLLECTION' and obj.instance_collection]
        memo[key]=any(contains_redirect(child,path|{key}) for child in children)
        return memo[key]
    from . import source_display
    _outline_roots[:]=list(dict.fromkeys([obj for obj in c.scene.objects if obj.instance_type=='COLLECTION' and obj.instance_collection and contains_redirect(obj.instance_collection)]+source_display.outline_roots()))


def evaluate(c):
    """Flush a complete display transition once, with reference handlers guarded."""
    global _busy,_dirty
    previous=_busy;_busy=True
    try:
        c.view_layer.update()
        rebuild_outline_roots(c)
        _dirty=False
    finally:_busy=previous


def reference_snapshot():
    """One operation-local scan for reference queries and change detection."""
    result={};links={};children={}
    for obj in bpy.data.objects:
        if obj.parent:children.setdefault(obj.parent,[]).append(obj)
        if obj.get(OWNED) or obj.get('sg_source_display'):continue
        source=obj.get(SOURCE) or obj.instance_collection
        if source:result.setdefault(source,[]).append(obj)
        col=obj.instance_collection
        if col:links[obj.as_pointer()]=(col.as_pointer(),obj.instance_type)
    return result,links,children


def remember_links(links):
    _instance_links.clear();_instance_links.update(links)
    for obj in _redirected.values():
        col=obj.instance_collection
        if col:_instance_links[obj.as_pointer()]=(col.as_pointer(),obj.instance_type)
    _collection_links.clear()
    for col in (*bpy.data.collections,*(scene.collection for scene in bpy.data.scenes)):
        if not col.get(OWNED):_collection_links[col.as_pointer()]=collection_signature(col)


def collection_signature(col):
    return (tuple(child.as_pointer() for child in col.children),
            tuple(obj.as_pointer() for obj in col.objects if obj.instance_collection),
            tuple(col.instance_offset))


def refresh(c,force=False,*,evaluate=True,references=None):
    global _busy,_dirty,_signature
    if _busy or _suspended:return
    from . import instances as f,source_edit
    work=f.session(c)
    if not work:
        other=source_edit.sessions()
        if other:
            scene,chain=other[0]
            with c.temp_override(scene=scene,view_layer=scene.view_layers[0]):refresh(c,force,evaluate=evaluate)
            return
    if not work:
        if _signature is not None:clear()
        return
    chain=f.session_chain(work)
    levels=chain
    signature=(c.scene.as_pointer(),tuple(w.as_pointer() for w in chain))
    if not force and not _dirty and signature==_signature:return
    _busy=True
    try:
        keys={level.as_pointer() for level in levels}
        if set(_records)-keys:clear(keep=levels);references=None
        wanted={};direct_wanted=set()
        from . import compensation,coordinates
        index,links,children=references if references is not None else reference_snapshot()
        for level in levels:
            target=level['sg_session_target'];source=level['sg_session_source']
            peers=[]
            offset=Matrix.Translation(level.instance_offset)
            transform=offset.inverted()@coordinates.frame(level).inverted()@offset
            inverse=transform.inverted()
            for peer in index.get(source,()):
                if peer==target or peer.library or peer.override_library:continue
                if compensation.apply(peer,level,transform,inverse,children.get(peer,())):direct_wanted.add(peer.as_pointer())
                else:peers.append(peer)
            if not peers:continue
            key=level.as_pointer()
            if key not in _records:_records[key]=bridge(level)
            _,proxy,root,carrier=_records[key]
            if carrier.matrix_parent_inverse!=transform:carrier.matrix_parent_inverse=transform
            for peer in peers:wanted[peer.as_pointer()]=(peer,source,proxy)
        compensation.prune(direct_wanted)
        for key,obj in list(_redirected.items()):
            if key in wanted:continue
            try:
                source=obj.get(SOURCE)
                if source:obj.instance_collection=source;del obj[SOURCE]
                if obj.instance_collection:links[key]=(obj.instance_collection.as_pointer(),obj.instance_type)
            except ReferenceError:pass
            del _redirected[key]
        for key,(peer,source,proxy) in wanted.items():
            if not peer.get(SOURCE):peer[SOURCE]=source
            if peer.instance_collection!=proxy:peer.instance_collection=proxy
            _redirected[key]=peer
        from . import source_display
        source_display.refresh(chain)
        remember_links(links)
        if evaluate:
            c.view_layer.update()
            rebuild_outline_roots(c)
        _signature=signature;_dirty=False
        if work.get('sg_session_preview_error'):work['sg_session_preview_error']=''
    except Exception as ex:
        clear()
        work['sg_session_preview_error']='关联显示暂停：'+str(ex)
        _signature=signature;_dirty=False
    finally:_busy=False


@persistent
def changed(scene,graph):
    if _busy or _suspended or not _enabled:return
    if not scene.get('sg_active_instance_session'):return
    for update in graph.updates:
        item=update.id.original
        if item.get(OWNED) or item.get('sg_source_display'):continue
        if isinstance(item,bpy.types.Collection):
            from . import source_display
            if source_display.structure_changed(item) or collection_signature(item)!=_collection_links.get(item.as_pointer()):mark();return
            continue
        if isinstance(item,bpy.types.Object):
            pointer=item.as_pointer()
            col=item.instance_collection
            if col or pointer in _instance_links:
                value=(col.as_pointer(),item.instance_type) if col else None
                if value!=_instance_links.get(pointer):mark();return


def tick():
    if not _enabled:return None
    if not _suspended:refresh(bpy.context)
    return .25 if any(scene.get('sg_active_instance_session') for scene in bpy.data.scenes) else 1.0


@persistent
def suspend(*args):
    global _suspended
    _suspended=True;clear()


@persistent
def resume(*args):
    global _suspended
    _suspended=False;clear(force=True)
    from . import source_edit
    source_edit.unpark_all()
    if source_edit.active(bpy.context.scene.get('sg_active_instance_session')):
        bpy.context.view_layer.update()
        refresh(bpy.context,True)


@persistent
def park_for_io(*args):
    from . import source_edit
    suspend()
    source_edit.park_all()


def handlers():
    return (
        (bpy.app.handlers.depsgraph_update_post,changed),
        (bpy.app.handlers.render_pre,park_for_io),(bpy.app.handlers.render_complete,resume),(bpy.app.handlers.render_cancel,resume),
        (bpy.app.handlers.save_pre,park_for_io),(bpy.app.handlers.save_post,resume),(bpy.app.handlers.save_post_fail,resume),
        (bpy.app.handlers.load_pre,suspend),(bpy.app.handlers.load_post,resume),
        (bpy.app.handlers.undo_pre,suspend),(bpy.app.handlers.undo_post,resume),
        (bpy.app.handlers.redo_pre,suspend),(bpy.app.handlers.redo_post,resume))


def register():
    global _enabled
    _enabled=True
    for items,callback in handlers():items.append(callback)
    bpy.app.timers.register(resume,first_interval=.1)
    if not bpy.app.background:bpy.app.timers.register(tick,first_interval=.25)


def unregister():
    global _enabled
    _enabled=False
    if bpy.app.timers.is_registered(resume):bpy.app.timers.unregister(resume)
    if bpy.app.timers.is_registered(tick):bpy.app.timers.unregister(tick)
    for items,callback in handlers():
        if callback in items:items.remove(callback)
    clear()
