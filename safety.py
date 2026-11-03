import math
import bpy

SUPPORTED_OBJECTS={'MESH','CURVE','SURFACE','FONT','EMPTY','LIGHT','CAMERA','LATTICE'}


def copy_id(source,ledger):
    value=source.copy()
    ledger.append(value)
    return value


def rollback(ledger):
    if ledger:
        bpy.data.batch_remove(ids=set(ledger))
        ledger.clear()


def matrix_check(matrix,label,invertible=False):
    if any(not math.isfinite(v) for row in matrix for v in row):
        raise ValueError(label+'：变换包含非有限数值')
    if invertible:
        try:matrix.inverted()
        except ValueError:raise ValueError(label+'：零缩放或不可逆变换') from None


def refs(struct,path='',depth=0):
    if depth>3:return
    for prop in struct.bl_rna.properties:
        key=prop.identifier
        if key=='rna_type':continue
        if prop.type=='POINTER':
            value=getattr(struct,key,None)
            if isinstance(value,bpy.types.ID):
                yield struct,key,value,prop.is_readonly,path+key
            elif value is not None and key not in {'animation_data'}:
                yield from refs(value,path+key+'.',depth+1)
        elif prop.type=='COLLECTION' and key=='projectors':
            for index,value in enumerate(getattr(struct,key)):
                yield from refs(value,path+f'{key}[{index}].',depth+1)


def object_refs(obj,data_fields=None):
    for mod in obj.modifiers:
        yield from refs(mod,mod.name+'.')
    if obj.data:
        data_fields={} if data_fields is None else data_fields
        kind=obj.data.bl_rna.identifier
        if kind not in data_fields:
            data_fields[kind]=tuple((p.identifier,p.is_readonly) for p in obj.data.bl_rna.properties if p.type=='POINTER')
        for key,readonly in data_fields[kind]:
            value=getattr(obj.data,key,None)
            if isinstance(value,(bpy.types.Object,bpy.types.Collection,bpy.types.Texture)):
                yield obj.data,key,value,readonly,'data.'+key
        if obj.type=='CAMERA':yield from refs(obj.data.dof,'camera.dof.')


def material_trees(material):
    pending=[material.node_tree] if material and material.node_tree else []
    seen=set()
    while pending:
        tree=pending.pop()
        if tree in seen:continue
        seen.add(tree);yield tree
        pending.extend(node.node_tree for node in tree.nodes if node.type=='GROUP' and node.node_tree)


def issues(objects,collections,allowed_parents=(),*,check_materials=True):
    objects=set(objects);collections=set(collections);allowed_parents=set(allowed_parents)
    problems=[];dependencies={o:set() for o in objects};checked_materials=set()
    data_fields={}
    for col in collections:
        if col.library or col.override_library:problems.append(col.name+'：库链接/覆盖集合需先本地化')
    for obj in objects:
        label=obj.name
        try:matrix_check(obj.matrix_world,label)
        except ValueError as ex:problems.append(str(ex))
        if obj.library or obj.override_library:problems.append(label+'：库链接/覆盖对象需先本地化')
        if obj.type not in SUPPORTED_OBJECTS:problems.append(label+'：暂未支持的对象类型 '+obj.type)
        if obj.animation_data or obj.constraints or obj.parent_type!='OBJECT':problems.append(label+'：含动画、驱动、约束或骨骼父级')
        if obj.rigid_body or obj.rigid_body_constraint:problems.append(label+'：刚体/刚体约束暂不支持')
        if obj.field and obj.field.type!='NONE':problems.append(label+'：力场暂不支持实例原地编辑')
        if obj.parent:
            if obj.parent not in objects and obj.parent not in allowed_parents:problems.append(label+'：父级位于当前组外')
        if obj.data and obj.data.animation_data:problems.append(label+'：对象数据含动画或驱动')
        if obj.type=='MESH' and obj.data.shape_keys:problems.append(label+'：形态键暂不支持隔离编辑')
        for slot in obj.material_slots if check_materials else ():
            material=slot.material
            if not material or material in checked_materials:continue
            checked_materials.add(material)
            if material.animation_data:problems.append(label+' / '+material.name+'：材质动画/驱动暂不支持隔离编辑')
            for tree in material_trees(material):
                if tree.animation_data:problems.append(label+' / '+tree.name+'：着色节点动画/驱动暂不支持隔离编辑')
                for node in tree.nodes:
                    for struct,key,value,readonly,path in refs(node):
                        if isinstance(value,(bpy.types.Object,bpy.types.Collection)):
                            problems.append(label+' / '+node.name+'：着色节点引用对象/集合，暂不支持原地重定向')
        for struct,key,value,readonly,path in object_refs(obj,data_fields):
            prefix=label+' / '+path
            if isinstance(value,bpy.types.Object):
                if value not in objects:
                    if not isinstance(struct,bpy.types.Modifier):problems.append(prefix+'：引用组外对象 '+value.name)
                else:
                    geometry_ref=isinstance(struct,bpy.types.Modifier) or key in {'start_cap','end_cap','curve','bevel_object','taper_object'}
                    if geometry_ref:dependencies[obj].add(value)
                    if readonly:problems.append(prefix+'：只读引用无法重定向')
            elif isinstance(value,bpy.types.Collection):
                if value not in collections or not set(value.all_objects)<=objects:
                    if not isinstance(struct,bpy.types.Modifier):problems.append(prefix+'：引用组外集合 '+value.name)
                else:
                    dependencies[obj].update(value.all_objects)
                    if readonly:problems.append(prefix+'：只读集合引用无法重定向')
            elif isinstance(value,bpy.types.Texture):
                if readonly:problems.append(prefix+'：只读纹理引用无法隔离')
    visiting=set();done=set()
    def cycle(obj):
        if obj in visiting:return True
        if obj in done:return False
        visiting.add(obj)
        for dependency in dependencies[obj]:
            if cycle(dependency):return True
        visiting.remove(obj);done.add(obj);return False
    for obj in objects:
        if cycle(obj):
            problems.append(obj.name+'：父级/修改器之间存在循环依赖');break
    return list(dict.fromkeys(problems))


def require_safe(objects,collections,allowed_parents=(),*,check_materials=True):
    problems=issues(objects,collections,allowed_parents,check_materials=check_materials)
    if problems:
        raise ValueError('；'.join(problems[:3])+ (f'；另有 {len(problems)-3} 项，请使用兼容性检查' if len(problems)>3 else ''))


def remap(objects_map,collections_map,ledger,textures=None):
    textures={} if textures is None else textures
    data_fields={}
    for obj in objects_map.values():
        for struct,key,value,readonly,path in object_refs(obj,data_fields):
            if isinstance(value,bpy.types.Object) and value in objects_map:
                setattr(struct,key,objects_map[value])
            elif isinstance(value,bpy.types.Collection) and value in collections_map:
                setattr(struct,key,collections_map[value])
            elif isinstance(value,bpy.types.Texture):
                if value not in textures:textures[value]=copy_id(value,ledger)
                setattr(struct,key,textures[value])
    return textures


def visibility(root,objects,attribute):
    visible=set()
    def visit(col,enabled):
        enabled=enabled and not getattr(col,attribute,False)
        if enabled:visible.update(col.objects)
        for child in col.children:visit(child,enabled)
    visit(root,True)
    return {obj:obj in visible and not getattr(obj,attribute,False) for obj in objects}


def check_incoming(objects,allowed_collections):
    objects=set(objects);allowed=set(allowed_collections)|objects|{o.data for o in objects if o.data}
    for scene in bpy.data.scenes:
        if objects.intersection(scene.objects):allowed.add(scene)
        if scene.camera in objects:raise ValueError(scene.camera.name+'：被场景用作活动相机，不能改变组边界')
        if scene.collection in allowed:allowed.add(scene)
    for obj,owners in bpy.data.user_map(subset=objects).items():
        # Blender retains the last active object in ParticleEditSettings even
        # outside particle mode. Ignore that cache only when it is the sole
        # scene reference; cameras, bake cages and other real users still block.
        bad=[owner.name for owner in owners if owner not in allowed and not (
            isinstance(owner,bpy.types.Scene) and owner.tool_settings.particle_edit.object==obj
            and owner.user_of_id(obj)==1)]
        if bad:raise ValueError(obj.name+'：被组外数据引用（'+', '.join(bad[:3])+'），不能直接改组边界')


def has_external_membership(objects,allowed_collections):
    objects=set(objects);allowed=set(allowed_collections)
    return any(col not in allowed and not col.get('sg_source_display_definition') and not objects.isdisjoint(col.objects)
               for col in (*bpy.data.collections,*(s.collection for s in bpy.data.scenes)))


class SGI_OT_audit(bpy.types.Operator):
    bl_idname='sketchgroup.audit'
    bl_label='检查组兼容性'
    bl_description='只读检查当前层的修改器、引用与变换；不会展开深层实例'
    @classmethod
    def poll(cls,c):
        from .instances import session,is_instance
        return bool(session(c) or is_instance(c.active_object))
    def collect(self,c):
        from .instances import session,collection_tree
        work=session(c);root=work or c.active_object.instance_collection
        cols=collection_tree(root);objects=set(root.all_objects)
        allowed=[work.get('sg_session_target')] if work else []
        self.lines=issues(objects,cols,allowed)
    def invoke(self,c,event):
        self.collect(c)
        return c.window_manager.invoke_props_dialog(self,width=650)
    def draw(self,c):
        lines=getattr(self,'lines',[])
        if not lines:self.layout.label(text='当前层检查通过；仍会在提交前复核引用和状态',icon='CHECKMARK')
        for line in lines[:16]:self.layout.label(text=line,icon='ERROR')
        if len(lines)>16:self.layout.label(text=f'另有 {len(lines)-16} 项；详细内容已输出到控制台')
    def execute(self,c):
        self.collect(c)
        for line in self.lines:print('SketchGroup compatibility:',line)
        self.report({'WARNING'} if self.lines else {'INFO'},f'发现 {len(self.lines)} 项兼容性问题' if self.lines else '当前层兼容性检查通过')
        return {'FINISHED'}
