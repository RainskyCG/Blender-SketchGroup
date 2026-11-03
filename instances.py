import bpy
from bpy.app.handlers import persistent
from . import focus, safety, structure, markers,preview
from .explode import SGI_OT_explode
from .selection_state import LOCK_TOKEN,LOCK_PREVIOUS,lock_object,unlock_object
from mathutils import Matrix, Vector
from bpy.props import BoolProperty, StringProperty

SESSION = 'sg_instance_session'
ACTIVE = 'sg_active_instance_session'
KEYS = []
_last_active={}


@persistent
def restore_orphan_locks(*args):
    live=set()
    for scene in bpy.data.scenes:
        work=scene.get(ACTIVE)
        if isinstance(work,bpy.types.Collection):
            live.update(w.get('sg_session_token') for w in session_chain(work))
    for obj in (*bpy.data.objects,*bpy.data.collections):
        token=obj.get(LOCK_TOKEN)
        if token and token not in live and not obj.library:unlock_object(obj)


@persistent
def check_session_lifecycle(scene,depsgraph):
    work=scene.get(ACTIVE)
    current=work.as_pointer() if isinstance(work,bpy.types.Collection) else None
    previous=_last_active.get(scene.as_pointer())
    _last_active[scene.as_pointer()]=current
    if previous and current is None and not bpy.app.timers.is_registered(restore_orphan_locks):
        bpy.app.timers.register(restore_orphan_locks,first_interval=.01)

def is_instance(o):
    return bool(o and o.instance_type == 'COLLECTION' and o.instance_collection)

def session(c):
    value = c.scene.get(ACTIVE)
    from .source_edit import active
    return value if active(value) else None

def session_chain(work):
    result=[]
    from .source_edit import active
    while active(work) and work not in result:
        result.append(work)
        work=work.get('sg_session_parent')
    return result

def within_scope(c,objects):
    work=session(c)
    return not work or all(o.name in work.all_objects and not o.get('sg_coordinate_anchor') for o in objects)

def choose(c, objects,*,update=True):
    if update:c.view_layer.update()
    objects=[o for o in objects if o.name in c.view_layer.objects and not o.hide_select and not o.hide_get() and not o.hide_viewport]
    for o in c.selected_objects: o.select_set(False)
    for o in objects:
        if o.name in c.view_layer.objects: o.select_set(True)
    c.view_layer.objects.active = objects[0] if objects else None

def include_local_view(c, objects):
    space = c.space_data
    if space and space.type == 'VIEW_3D' and space.local_view:
        for o in objects: o.local_view_set(space,True)

def collection_tree(root):
    result = {root}
    for child in root.children:
        if not child.get(SESSION):result.update(collection_tree(child))
    return result


def scope_objects(root):
    return {obj for col in collection_tree(root) for obj in col.objects}


def unlink_session(scene,work):
    if work.get('sg_session_scene_links'):
        container=work.get('sg_session_container')
        if container and work.name in container.children:container.children.unlink(work)
        return
    containers={scene.collection,work.get('sg_session_parent'),work.get('sg_session_container')}
    for col in containers:
        if col and work.name in col.children:col.children.unlink(work)


def source_graph(root, visited=None):
    visited = set() if visited is None else visited
    if root in visited: return visited
    visited.add(root)
    for child in root.children:
        if not child.get(SESSION):source_graph(child, visited)
    for o in root.objects:
        if is_instance(o): source_graph(o.instance_collection, visited)
    return visited

def validate_source(root,deep=True):
    return validate_definitions((root,),deep=deep)


def validate_definitions(roots,deep=True,check_materials=True):
    roots=tuple(roots);cols=set()
    for root in roots:
        if deep:source_graph(root,cols)
        else:cols.update(collection_tree(root))
    objects={o for col in cols for o in col.objects}
    safety.require_safe(objects,cols,check_materials=check_materials)
    return cols,objects


def clone_definition(root,deep=True,ledger=None):
    return clone_definitions((root,),deep=deep,ledger=ledger)[root]


def clone_definitions(roots,deep=True,ledger=None,*,copy_materials=True):
    cols,objects=validate_definitions(roots,deep=deep,check_materials=copy_materials)
    own=ledger is None
    ledger=[] if own else ledger
    try:
        cm={col:safety.copy_id(col,ledger) for col in cols}
        om={o:safety.copy_id(o,ledger) for o in objects}
        dm,mm,groups={},{},{}
        for old,new in cm.items():
            new['sg_owned_definition']=True
            for o in list(new.objects):new.objects.unlink(o)
            for child in list(new.children):new.children.unlink(child)
            for o in old.objects:new.objects.link(om[o])
            for child in old.children:
                if child in cm:new.children.link(cm[child])
        def copy_groups(tree):
            if not tree:return
            for node in tree.nodes:
                if node.type!='GROUP' or not node.node_tree:continue
                old=node.node_tree
                if old not in groups:
                    groups[old]=safety.copy_id(old,ledger)
                    copy_groups(groups[old])
                node.node_tree=groups[old]
        def material(mat):
            if mat not in mm:
                mm[mat]=safety.copy_id(mat,ledger)
                copy_groups(mm[mat].node_tree)
            return mm[mat]
        for old,new in om.items():
            if new.get(LOCK_TOKEN):unlock_object(new)
            new.parent=om.get(old.parent)
            new.matrix_parent_inverse=old.matrix_parent_inverse.copy()
            new.matrix_basis=old.matrix_basis.copy()
            if old.data:
                if old.data not in dm:
                    data=dm[old.data]=safety.copy_id(old.data,ledger)
                    if copy_materials and hasattr(data,'materials'):
                        for i,mat in enumerate(old.data.materials):
                            if mat:data.materials[i]=material(mat)
                new.data=dm[old.data]
                if copy_materials:
                    for i,slot in enumerate(old.material_slots):
                        if slot.link=='OBJECT' and slot.material:
                            new.material_slots[i].material=material(slot.material)
            if is_instance(old):
                new.instance_collection=cm.get(old.instance_collection,old.instance_collection) if deep else old.instance_collection
        safety.remap(om,cm,ledger)
        return cm
    except Exception:
        if own:safety.rollback(ledger)
        raise

def layer_for(layer, collection):
    if layer.collection == collection: return layer
    for child in layer.children:
        result = layer_for(child, collection)
        if result: return result

def set_layer(c, collection):
    found = layer_for(c.view_layer.layer_collection, collection)
    if found: c.view_layer.active_layer_collection = found

def clear_session(c, work):
    parent=work.get('sg_session_parent')
    if parent and parent.get(SESSION):c.scene[ACTIVE]=parent
    elif ACTIVE in c.scene: del c.scene[ACTIVE]
    for key in list(work.keys()):
        if key.startswith('sg_session_') or key == SESSION: del work[key]


def check_instance(o,allow_animation=False):
    if not is_instance(o): raise ValueError('请选择集合实例')
    if o.type!='EMPTY':raise ValueError('暂仅支持空物体承载的集合实例，避免丢失实例根自带的几何')
    if o.library or o.override_library or len(o.users_scene) > 1:
        raise ValueError('请选择单场景内的本地实例')
    safety.matrix_check(o.matrix_world,o.name,invertible=True)
    if any(col.library or col.override_library for col in o.users_collection):raise ValueError(o.name+'：所在集合不可写')
    from .animation import check_chain
    check_chain(o,allow_animation)

class SGI_OT_enter(bpy.types.Operator):
    bl_idname = 'sketchgroup.instance_enter'
    bl_label = '编辑组'
    bl_description = '按当前姿态原位编辑源集合；动画关键帧保留，编辑期间不跟随时间轴'
    bl_options = {'REGISTER','UNDO'}
    @classmethod
    def poll(cls,c): return c.mode == 'OBJECT' and is_instance(c.active_object) and within_scope(c,[c.active_object])
    def execute(self,c):
        from . import source_edit
        if any(scene!=c.scene for scene,chain in source_edit.sessions()):
            self.report({'WARNING'},'请先退出另一场景的组编辑')
            return {'CANCELLED'}
        try:
            source_edit.enter(c)
            return {'FINISHED'}
        except (ValueError,RuntimeError) as ex:
            self.report({'WARNING'},'无法进入，已保留原实例：'+str(ex))
            return {'CANCELLED'}
def double_click_conflicts(c,event=None):
    from . import keymaps
    if event is None:
        from types import SimpleNamespace
        event=SimpleNamespace(type='LEFTMOUSE',value='DOUBLE_CLICK')
    found=set()
    for config in (c.window_manager.keyconfigs.user,):
        if not config:continue
        for name in ('Object Mode','3D View','3D View Generic'):
            keymap=config.keymaps.get(name)
            if not keymap:continue
            for item in keymap.keymap_items:
                if not keymaps.event_matches(item,event):continue
                if item.idname in {'sketchgroup.double_click_enter','view3d.select'}:continue
                found.add(item.idname)
    return sorted(found)


def pick_under_mouse(c,event):
    selected=tuple(c.selected_objects);active=c.view_layer.objects.active
    try:
        # Native selection cycles past the active selected base at a stationary
        # cursor. A neutral active base makes this probe return the nearest hit.
        # Keep Blender's GPU picker for instances, visibility and non-mesh types.
        c.view_layer.objects.active=None
        bpy.ops.view3d.select('EXEC_DEFAULT',False,
            location=(event.mouse_region_x,event.mouse_region_y),
            deselect_all=True,extend=False,deselect=False,toggle=False,
            select_passthrough=False,center=False,enumerate=False,object=False)
        return c.active_object if c.active_object and c.active_object.select_get() else None
    finally:
        for obj in tuple(c.selected_objects):
            if obj not in selected:obj.select_set(False)
        for obj in selected:
            if not obj.select_get():obj.select_set(True)
        c.view_layer.objects.active=active


class SGI_OT_double_click_enter(bpy.types.Operator):
    bl_idname='sketchgroup.double_click_enter'
    bl_label='双击编辑组'
    bl_options={'UNDO'}
    bl_description='编辑指针下的组；默认左键双击，可在插件按键映射中修改，遇到其他相同绑定时让行'
    @classmethod
    def poll(cls,c):
        return (c.area is not None and c.area.type=='VIEW_3D' and c.region is not None
                and c.region.type=='WINDOW' and c.mode=='OBJECT')
    def invoke(self,c,event):
        from . import keymaps
        if not keymaps.accepts(c,'sketchgroup.double_click_enter',event):
            return {'PASS_THROUGH'}
        if double_click_conflicts(c,event):return {'PASS_THROUGH'}
        from . import interaction
        hit=interaction.window_region(c,event)
        if not hit or hit[0]!=c.area or hit[1]!=c.region:return {'PASS_THROUGH'}
        target=pick_under_mouse(c,event)
        if not is_instance(target) or not within_scope(c,[target]):
            return {'PASS_THROUGH'}
        # The first click may already have cycled to an object behind the group.
        # Only the confirmed group hit changes selection; blank/non-group hits do not.
        choose(c,[target])
        return bpy.ops.sketchgroup.instance_enter('EXEC_DEFAULT')


def validate_finish(work):
    from . import coordinates
    if not work.get('sg_session_target'):raise ValueError('会话对象缺失，请先撤销删除')
    cols,objects=validate_source(work,deep=False)
    safety.matrix_check(coordinates.frame(work),'编辑坐标',invertible=True)
    if safety.has_external_membership(objects,cols):raise ValueError('编辑对象被链接到源集合外，请先整理归属')


class SGI_OT_finish(bpy.types.Operator):
    bl_idname = 'sketchgroup.instance_finish'
    bl_label = '退出组'
    bl_description = '退出当前编辑层；源集合修改实时生效'
    bl_options = {'REGISTER','UNDO'}
    @classmethod
    def poll(cls,c):return session(c) is not None and c.mode == 'OBJECT'
    def execute(self,c):
        from . import source_edit
        try:
            source_edit.leave(c)
            return {'FINISHED'}
        except (ValueError,RuntimeError) as ex:
            self.report({'WARNING'},str(ex))
            return {'CANCELLED'}


class SGI_OT_duplicate(bpy.types.Operator):
    bl_idname = 'sketchgroup.instance_duplicate'
    bl_label = '复制集合实例'
    bl_options = {'REGISTER','UNDO'}
    linked: BoolProperty(name='关联复制',default=False)
    @classmethod
    def poll(cls,c):
        return c.mode == 'OBJECT' and within_scope(c,c.selected_objects) and any(is_instance(o) for o in c.selected_objects)
    @preview.restore_after
    def execute(self,c):
        items = list(c.selected_objects)
        preview.clear()
        definitions={};ledger=[]
        try:
            if not self.linked:
                definitions=clone_definitions(
                    {o.instance_collection for o in items if is_instance(o)},
                    deep=False,ledger=ledger,copy_materials=False)
            result=bpy.ops.object.duplicate(linked=self.linked)
            if result!={'FINISHED'}:
                safety.rollback(ledger)
                return result
        except Exception as ex:
            safety.rollback(ledger)
            self.report({'ERROR'},'复制失败，已回滚：'+str(ex));return {'CANCELLED'}
        for o in c.selected_objects:
            if not is_instance(o):continue
            if not self.linked:
                o.instance_collection=definitions[o.instance_collection]
        return {'FINISHED'}
    def invoke(self,c,event):
        result = self.execute(c)
        if result == {'FINISHED'}: bpy.ops.transform.translate('INVOKE_DEFAULT')
        return result

class SGI_OT_make_unique(bpy.types.Operator):
    bl_idname='sketchgroup.instance_make_unique'
    bl_label='独立化'
    bl_description='将选中组的当前层与其他实例解除关联；保留位置、嵌套组关联和共享材质'
    bl_options={'REGISTER','UNDO'}
    @classmethod
    def poll(cls,c):return SGI_OT_duplicate.poll(c)
    @preview.restore_after
    def execute(self,c):
        preview.clear()
        targets=[o for o in c.selected_objects if is_instance(o)]
        saved={o:o.instance_collection for o in targets}
        ledger=[]
        try:
            if any(o.library or o.override_library for o in targets):
                raise ValueError('请先将库链接或覆盖的实例本地化')
            roots={col for col in saved.values() if col.users-int(col.use_fake_user)>1}
            definitions=clone_definitions(roots,deep=False,ledger=ledger,copy_materials=False)
            for obj,source in saved.items():
                if source in definitions:obj.instance_collection=definitions[source]
        except Exception as ex:
            for obj,source in saved.items():
                if obj.instance_collection!=source:obj.instance_collection=source
            safety.rollback(ledger)
            self.report({'WARNING'},'无法独立化，已保留原组：'+str(ex));return {'CANCELLED'}
        return {'FINISHED'}


class SGI_OT_create(bpy.types.Operator):
    bl_idname = 'sketchgroup.instance_create'
    bl_label = '打组为集合实例'
    bl_options = {'REGISTER','UNDO'}
    name: StringProperty(name='名称',default='',options={'SKIP_SAVE'})
    @classmethod
    def poll(cls,c): return c.mode == 'OBJECT' and bool(c.selected_objects) and within_scope(c,c.selected_objects)
    def invoke(self,c,event): return self.execute(c)
    @preview.restore_after
    def execute(self,c):
        from . import preview
        selected = set(c.selected_objects)
        active = c.view_layer.objects.active
        group_name = self.name.strip() or (active.name if active in selected else min((o.name for o in selected),default='Group'))
        preview.clear()
        items,top=structure.object_tree(selected)
        parents={o.parent for o in top}
        if len(parents)!=1 or any(o.get('sg_coordinate_anchor') for o in items):
            self.report({'WARNING'},'请选择同一父级下的对象，不要选择内部坐标辅助对象');return {'CANCELLED'}
        parent=top[0].parent
        if any(o.animation_data or o.constraints or o.parent_type!='OBJECT' for o in top):
            self.report({'WARNING'},'暂不支持重设动画、约束或骨骼父级');return {'CANCELLED'}
        if any(o.library or o.override_library or len(o.users_scene)>1 for o in items):
            self.report({'WARNING'},'不支持链接或跨场景共享对象');return {'CANCELLED'}
        try:
            if parent:safety.matrix_check(parent.matrix_world,'父级',invertible=True)
            containers={col for o in items for col in o.users_collection}
            safety.require_safe(items,set(),[parent] if parent else [])
            safety.check_incoming(items,containers)
            if not within_scope(c,items):raise ValueError('选择的子层级跨越当前编辑边界')
            if any(col.library or col.override_library for col in containers):raise ValueError('对象所在集合不可写')
            if any(len(o.users_collection)>1 for o in items):raise ValueError('对象链接到多个集合，请先整理集合归属再打组')
        except (ValueError,RuntimeError) as ex:
            self.report({'WARNING'},str(ex));return {'CANCELLED'}
        c.view_layer.update()
        origin = sum((o.matrix_world.translation for o in selected),Vector()) / len(selected)
        ledger=[]
        memberships={o:list(o.users_collection) for o in items}
        transforms={o:(o.parent,o.matrix_parent_inverse.copy(),o.matrix_basis.copy()) for o in top}
        try:
            col = bpy.data.collections.new(group_name)
            ledger.append(col)
            col['sg_owned_definition'] = True
            col.instance_offset = origin
            instance = bpy.data.objects.new(col.name,None)
            ledger.append(instance)
            work=session(c)
            destination=c.collection if not work or c.collection in collection_tree(work) else work
            destination.objects.link(instance)
            instance.instance_type='COLLECTION';instance.instance_collection=col
            instance.empty_display_type='PLAIN_AXES'
            markers.set_center(instance,False)
            position=Matrix.Translation(origin)
            if parent:
                instance.parent=parent
                instance.matrix_parent_inverse=parent.matrix_world.inverted() @ position
                instance.matrix_basis=Matrix.Identity(4)
            else:instance.matrix_world=position
            from . import coordinates
            coordinates.write(col,{o:o.matrix_world.copy() for o in top},ledger)
            for o in items:
                col.objects.link(o)
                for old in list(o.users_collection):
                    if old != col: old.objects.unlink(o)
            include_local_view(c,[instance])
            choose(c,[instance])
            return {'FINISHED'}
        except Exception as ex:
            for obj,collections in memberships.items():
                for old in collections:
                    if obj.name not in old.objects:old.objects.link(obj)
                for col in list(obj.users_collection):
                    if col not in collections:col.objects.unlink(obj)
            for obj,(old_parent,inverse,basis) in transforms.items():
                obj.parent=old_parent;obj.matrix_parent_inverse=inverse;obj.matrix_basis=basis
            safety.rollback(ledger)
            choose(c,selected)
            self.report({'WARNING'},'打组失败，已恢复原对象：'+str(ex))
            return {'CANCELLED'}


class SGI_OT_select_linked(bpy.types.Operator):
    bl_idname='sketchgroup.select_linked'
    bl_label='选择关联组'
    bl_description='选择当前编辑范围内可见、可选的同源组，保留当前活动组'
    bl_options={'UNDO'}
    @classmethod
    def poll(cls,c):return c.mode=='OBJECT' and is_instance(c.active_object) and within_scope(c,[c.active_object])
    def execute(self,c):
        target=c.active_object;source=target.get(preview.SOURCE) or target.instance_collection
        work=session(c);scope=scope_objects(work) if work else c.view_layer.objects
        selectable=set(c.selectable_objects)
        objects=[obj for obj in scope if obj in selectable and is_instance(obj)
                 and (obj.get(preview.SOURCE) or obj.instance_collection)==source]
        if not objects:return {'CANCELLED'}
        choose(c,objects,update=False)
        if target in objects:c.view_layer.objects.active=target
        return {'FINISHED'}


class SGI_OT_center_marker(bpy.types.Operator):
    bl_idname='sketchgroup.center_marker'
    bl_label='组中心显示'
    bl_description='调整实例中心标记；不隐藏实例内容，不改变组的位置或变换'
    bl_options={'REGISTER','UNDO'}
    axes: BoolProperty(name='显示轴线',default=False)
    @classmethod
    def poll(cls,c):
        return (c.mode=='OBJECT' and bool(c.selected_objects) and within_scope(c,c.selected_objects)
                and all(is_instance(o) and o.type=='EMPTY' and not o.library and not o.override_library for o in c.selected_objects))
    def execute(self,c):
        for obj in c.selected_objects:markers.set_center(obj,self.axes)
        return {'FINISHED'}



CLASSES=(SGI_OT_enter,SGI_OT_double_click_enter,SGI_OT_finish,SGI_OT_duplicate,SGI_OT_make_unique,SGI_OT_create,SGI_OT_select_linked,SGI_OT_center_marker,SGI_OT_explode,safety.SGI_OT_audit)+structure.CLASSES

def prioritize_create_key():
    # Blender prepends each addon item when merging: the last addon binding
    # gets first priority. Run once after startup addons have registered.
    for index,(km,item) in enumerate(KEYS):
        if item.idname!='sketchgroup.instance_create':continue
        entries=list(km.keymap_items);position=entries.index(item)
        if any(k.type=='G' and k.ctrl and not k.alt and not k.shift for k in entries[position+1:]):
            replacement=km.keymap_items.new_from_item(item,head=False)
            km.keymap_items.remove(item);KEYS[index]=(km,replacement)
        break


def register():
    bpy.app.handlers.depsgraph_update_post.append(check_session_lifecycle)
    for handlers in (bpy.app.handlers.undo_post,bpy.app.handlers.redo_post,bpy.app.handlers.load_post):
        handlers.append(restore_orphan_locks)
    bpy.app.timers.register(restore_orphan_locks,first_interval=.1)
    for cls in CLASSES:bpy.utils.register_class(cls)
    focus.register()
    kc=bpy.context.window_manager.keyconfigs.addon
    if kc:
        km=kc.keymaps.new(name='Object Mode',space_type='EMPTY')
        KEYS.append((km,km.keymap_items.new('sketchgroup.double_click_enter','LEFTMOUSE','DOUBLE_CLICK')))
        for op,key,mods,props in [
            ('sketchgroup.instance_enter','DOWN_ARROW',{'alt':True},{}),
            ('sketchgroup.instance_finish','UP_ARROW',{'alt':True},{}),
            ('sketchgroup.instance_duplicate','D',{'shift':True},{'linked':False}),
            ('sketchgroup.instance_duplicate','D',{'alt':True},{'linked':True}),
            ('sketchgroup.instance_create','G',{'ctrl':True},{}),
            ('sketchgroup.instance_join','J',{'ctrl':True},{}),
            ('sketchgroup.separate_menu','P',{},{}),
            ('sketchgroup.tab_edit','TAB',{},{}),
            ('sketchgroup.escape_exit','ESC',{},{}),
        ]:
            k=km.keymap_items.new(op,key,'PRESS',**mods)
            for name,value in props.items():setattr(k.properties,name,value)
            KEYS.append((km,k))
        bpy.app.timers.register(prioritize_create_key,first_interval=.1)

def recover_on_disable():
    from . import source_edit,source_display,selection_state,outliner
    for scene,chain in source_edit.sessions():
        for layer in scene.view_layers:
            obj=layer.objects.active
            if obj and obj.mode!='OBJECT':
                with bpy.context.temp_override(scene=scene,view_layer=layer):bpy.ops.object.mode_set(mode='OBJECT')
        for work in chain:
            source_edit.park(scene,work)
            selection_state.restore(work,clear=True)
            target=work.get('sg_session_target')
            if target:unlock_object(target,work.get('sg_session_select_lock',False))
            outliner.restore_colors(work);source_display.finish(work)
            for key in list(work.keys()):
                if key.startswith('sg_session_') or key==SESSION:del work[key]
        if ACTIVE in scene:del scene[ACTIVE]


def unregister():
    if bpy.app.timers.is_registered(prioritize_create_key):bpy.app.timers.unregister(prioritize_create_key)
    focus.unregister()
    recover_on_disable()
    restore_orphan_locks()
    if check_session_lifecycle in bpy.app.handlers.depsgraph_update_post:bpy.app.handlers.depsgraph_update_post.remove(check_session_lifecycle)
    _last_active.clear()
    if bpy.app.timers.is_registered(restore_orphan_locks):bpy.app.timers.unregister(restore_orphan_locks)
    for handlers in (bpy.app.handlers.undo_post,bpy.app.handlers.redo_post,bpy.app.handlers.load_post):
        if restore_orphan_locks in handlers:handlers.remove(restore_orphan_locks)
    for km,k in KEYS:km.keymap_items.remove(k)
    KEYS.clear()
    for cls in reversed(CLASSES):bpy.utils.unregister_class(cls)


