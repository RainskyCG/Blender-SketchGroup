import bpy
from bpy.props import BoolProperty
from . import preview
from mathutils import Matrix

def close_matrix(a,b):
    return max(abs(a[i][j]-b[i][j]) for i in range(4) for j in range(4))<1e-5

def simple_matrix(matrix):
    loc,rot,scale=matrix.decompose()
    return Matrix.LocRotScale(loc,rot,scale)

def meaningful_parent(o,objects):
    parent=o.parent
    while parent and parent.get('sg_coordinate_anchor'):parent=parent.parent
    return parent if parent in objects else None

def cleanup_owned(root):
    from .instances import source_graph
    candidates=source_graph(root)
    pending_objects=set()
    changed=True
    while changed:
        changed=False
        for col in list(candidates):
            if not col.get('sg_owned_definition') or col.users!=0:continue
            objects=list(col.objects)
            pending_objects.update(objects)
            candidates.remove(col)
            bpy.data.collections.remove(col)
            changed=True
        # Children may still count as users of an anchor until they are removed.
        # Revisit orphan candidates so deletion order cannot leave helper empties.
        for obj in list(pending_objects):
            if obj.users==0:
                pending_objects.remove(obj);bpy.data.objects.remove(obj);changed=True

def build_plan(target,source,editing=False):
    from .instances import check_instance,validate_source
    from . import safety
    if not editing:check_instance(target)
    else:
        from .animation import check_chain
        check_chain(target)
        safety.matrix_check(target.matrix_world,target.name,invertible=True)
    allowed=list(target.users_collection)+([source] if editing else [])
    attached=set(source.all_objects) if editing else set()
    if editing:allowed.extend(attached)
    safety.check_incoming([target],allowed)
    if any(child not in attached for child in target.children):raise ValueError(target.name+'：实例含外部子对象，不能安全解散')
    validate_source(source,deep=False)
    objects={o for o in source.all_objects if not o.get('sg_coordinate_anchor')}
    for obj in objects:
        for struct,key,value,readonly,path in safety.object_refs(obj):
            if isinstance(value,bpy.types.Object) and value.get('sg_coordinate_anchor'):
                raise ValueError(obj.name+' / '+path+'：引用插件坐标辅助对象，请先改用组内参照对象')
    transform=target.matrix_world@Matrix.Translation(-source.instance_offset)
    local_world={}
    for obj in objects:
        chain=[];current=obj
        while current and current not in local_world:
            chain.append(current);current=current.parent
        parent_world=local_world.get(current,Matrix.Identity(4))
        for current in reversed(chain):
            parent_world=(parent_world@current.matrix_parent_inverse@current.matrix_basis
                          if current.parent else current.matrix_basis.copy())
            local_world[current]=parent_world
    # Unlinked instance-source empties may retain stale matrix_world values.
    # Validated static source transforms are authoritative in their local chain.
    world={o:(local_world[o] if editing else transform@local_world[o]) for o in objects}
    parents={o:meaningful_parent(o,objects) for o in objects}
    bake=set()
    for o in objects:
        parent=parents[o]
        parent_world=world[parent] if parent else (target.parent.matrix_world if target.parent else None)
        if parent_world is not None:safety.matrix_check(parent_world,o.name+' 父级',invertible=True)
        safety.matrix_check(world[o],o.name)
        if parent_world is None and not close_matrix(world[o],simple_matrix(world[o])):
            if o.type!='MESH':raise ValueError(o.name+'：非网格剪切不能无损解散')
            safety.matrix_check(simple_matrix(world[o]),o.name,invertible=True)
            for mod in o.modifiers:
                if mod.show_viewport!=mod.show_render or (mod.type=='SUBSURF' and mod.levels!=mod.render_levels):
                    raise ValueError(o.name+' / '+mod.name+'：视口与渲染效果不同，剪切烘焙会损失渲染结果，请先统一设置')
            bake.add(o)
    return target,source,objects,world,parents,bake

class SGI_OT_explode(bpy.types.Operator):
    bl_idname='sketchgroup.instance_explode'
    bl_label='解散组'
    bl_description='原地展开所选组；保留静态修改器和内部引用，清除插件辅助对象'
    bl_options={'REGISTER','UNDO'}
    recursive: BoolProperty(name='解散全部',description='递归展开所选组内所有嵌套组',default=False)
    @classmethod
    def poll(cls,c):
        from .instances import is_instance,within_scope
        return c.mode=='OBJECT' and bool(c.selected_objects) and within_scope(c,c.selected_objects) and all(is_instance(o) for o in c.selected_objects)
    @preview.restore_after
    def execute(self,c):
        from . import preview
        preview.clear()
        from .instances import choose,include_local_view,is_instance
        from . import safety
        targets=list(c.selected_objects);plans=[]
        c.view_layer.update()
        try:
            for target in targets:
                plans.append(build_plan(target,target.instance_collection))
        except (ValueError,RuntimeError) as ex:
            self.report({'WARNING'},str(ex));return {'CANCELLED'}
        result=[];baked=0;ledger=[]
        try:
            # All outputs are prepared before deleting any input instance.
            frontier=[(plan,frozenset((plan[1],))) for plan in plans]
            while frontier:
                children=[]
                for plan,ancestry in frontier:
                    target,source,objects,world,parents,bake=plan
                    copies={o:safety.copy_id(o,ledger) for o in objects}
                    data={};collections={}
                    render=safety.visibility(source,objects,'hide_render')
                    viewport=safety.visibility(source,objects,'hide_viewport')
                    selectable=safety.visibility(source,objects,'hide_select')
                    for old,new in copies.items():
                        if old.data:
                            if old.data not in data:data[old.data]=safety.copy_id(old.data,ledger)
                            new.data=data[old.data]
                        new.parent=None;new.matrix_parent_inverse=Matrix.Identity(4)
                        for col in target.users_collection:col.objects.link(new)
                        new.hide_render=target.hide_render or not render[old]
                        new.hide_viewport=target.hide_viewport or not viewport[old]
                        new.hide_select=target.hide_select or not selectable[old]
                        for struct,key,value,readonly,path in safety.object_refs(old):
                            if isinstance(value,bpy.types.Collection) and value not in collections:
                                operand=bpy.data.collections.new('Operand · '+value.name);ledger.append(operand)
                                collections[value]=operand
                                target.users_collection[0].children.link(operand)
                                for member in value.all_objects:
                                    if member in copies:operand.objects.link(copies[member])
                    safety.remap(copies,collections,ledger)
                    pending=set(objects);ordered=[]
                    while pending:
                        batch=[o for o in pending if parents[o] not in pending]
                        if not batch:raise ValueError('父级循环，不能安全解散')
                        ordered.extend(batch);pending.difference_update(batch)
                    actual={}
                    for old in ordered:
                        new=copies[old];parent=copies.get(parents[old],target.parent)
                        if parent:
                            parent_world=actual[parents[old]] if parents[old] else target.parent.matrix_world
                            new.parent=parent
                            new.matrix_parent_inverse=parent_world.inverted()@world[old]
                            new.matrix_basis=Matrix.Identity(4)
                            actual[old]=world[old]
                        else:
                            simple=simple_matrix(world[old])
                            if old in bake:
                                graph=c.evaluated_depsgraph_get()
                                mesh=bpy.data.meshes.new_from_object(old.evaluated_get(graph),preserve_all_data_layers=True,depsgraph=graph)
                                ledger.append(mesh)
                                mesh.transform(simple.inverted()@world[old]);mesh.update()
                                new.modifiers.clear();new.data=mesh;baked+=1
                            new.matrix_world=simple;actual[old]=simple
                    for obj in copies.values():
                        if self.recursive and is_instance(obj):
                            child_source=obj.instance_collection
                            if child_source in ancestry:raise ValueError('集合实例循环引用，不能递归解散')
                            children.append((obj,child_source,ancestry|{child_source}))
                        else:result.append(obj)
                frontier=[]
                if children:
                    c.view_layer.update()
                    for obj,source,ancestry in children:
                        plan=build_plan(obj,source)
                        plans.append(plan);frontier.append((plan,ancestry))
            c.view_layer.update()
        except Exception as ex:
            safety.rollback(ledger)
            self.report({'ERROR'},'解散失败，原实例保留且新增数据已回滚：'+str(ex));return {'CANCELLED'}
        sources={plan[1].name for plan in plans}
        for target,source,objects,world,parents,bake in plans:
            bpy.data.objects.remove(target,do_unlink=True)
        for name in sources:
            source=bpy.data.collections.get(name)
            if source:cleanup_owned(source)
        include_local_view(c,result);choose(c,result)
        self.report({'INFO'},f'已原地解散 {len(result)} 个对象，无插件辅助空物体'+(f'；{baked} 个剪切网格已烘焙' if baked else ''))
        return {'FINISHED'}
