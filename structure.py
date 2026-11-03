import bpy
from bpy.props import BoolProperty
from . import preview
from mathutils import Matrix


def api():
    from . import instances
    return instances


def world_parent(obj,parent,world):
    obj.parent=parent
    if parent:
        obj.matrix_parent_inverse=parent.matrix_world.inverted()@world
        obj.matrix_basis=Matrix.Identity(4)
    else:
        obj.matrix_parent_inverse=Matrix.Identity(4)
        obj.matrix_world=world


def object_tree(selected):
    objects=set(selected);children={}
    for obj in bpy.data.objects:
        if obj.parent:children.setdefault(obj.parent,[]).append(obj)
    pending=list(objects)
    while pending:
        for child in children.get(pending.pop(),()):
            if child not in objects:objects.add(child);pending.append(child)
    return objects,[o for o in objects if o.parent not in objects]


def selected_tree(c):return object_tree(c.selected_objects)


def validate_move(c,objects,top):
    f=api()
    if not objects or not f.within_scope(c,objects):raise ValueError('请选择当前组内的对象')
    if any(o.get('sg_coordinate_anchor') for o in objects):raise ValueError('不能移动内部坐标辅助对象')
    if any(o.library or o.override_library or len(o.users_scene)>1 for o in objects):raise ValueError('链接、覆盖或跨场景对象请先本地化')
    if any(len(o.users_collection)!=1 for o in objects):raise ValueError('对象属于多个集合，请先整理集合归属')
    cols={col for o in objects for col in o.users_collection}
    f.safety.require_safe(objects,cols,[o.parent for o in top if o.parent])
    f.safety.check_incoming(objects,cols)


class SGI_OT_join(bpy.types.Operator):
    bl_idname='sketchgroup.instance_join'
    bl_label='加入组'
    bl_description='加入活动组；活动项为普通对象时以其原点重设组中心，关联组同步更新'
    bl_options={'REGISTER','UNDO'}
    @classmethod
    def poll(cls,c):
        f=api()
        return c.mode=='OBJECT' and len(c.selected_objects)>1 and any(f.is_instance(o) for o in c.selected_objects) and f.within_scope(c,c.selected_objects)
    @preview.restore_after
    def execute(self,c):
        f=api()
        from . import preview
        preview.clear()
        selected=list(c.selected_objects);active=c.active_object
        groups=[o for o in selected if f.is_instance(o)]
        target=active if active in groups else (groups[0] if len(groups)==1 else None)
        if not target:
            self.report({'WARNING'},'选中多个组时，请将目标组设为活动项');return {'CANCELLED'}
        c.view_layer.update();original_work=f.session(c)
        objects,top=object_tree(set(selected)-{target})
        try:
            f.check_instance(target);source=target.instance_collection
            if target in objects or target.children:raise ValueError('目标组与加入对象不能互为父子')
            if any(o.parent!=target.parent for o in top):raise ValueError('请选择与目标组位于同一父级的对象')
            validate_move(c,objects,top)
            f.validate_source(source,deep=False)
            if any(f.is_instance(o) and source in f.source_graph(o.instance_collection) for o in objects):raise ValueError('加入后会形成循环实例，操作已取消')
            peers=[o for o in bpy.data.objects if o.instance_collection==source]
            offset=source.instance_offset.copy()
            pivot=active.matrix_world.translation.copy() if active else target.matrix_world.translation.copy()
            delta=target.matrix_world.to_3x3().inverted()@(pivot-target.matrix_world.translation)
            if delta.length>1e-8:
                for peer in peers:
                    f.check_instance(peer)
                    if peer.parent in peers:raise ValueError('关联实例互为父子时暂不支持同步改变中心')
            peer_world={o:o.matrix_world.copy() for o in peers}
        except (ValueError,RuntimeError) as ex:
            self.report({'WARNING'},str(ex));return {'CANCELLED'}
        saved={o:(list(o.users_collection),o.parent,o.matrix_parent_inverse.copy(),o.matrix_basis.copy()) for o in objects}
        world={o:o.matrix_world.copy() for o in top}
        committed=False
        try:
            f.choose(c,[target])
            if bpy.ops.sketchgroup.instance_enter('EXEC_DEFAULT')!={'FINISHED'}:raise ValueError('无法进入目标组')
            work=f.session(c)
            for obj in objects:
                work.objects.link(obj)
                for col in list(obj.users_collection):
                    if col!=work:col.objects.unlink(obj)
                if obj.get(f.LOCK_TOKEN):f.unlock_object(obj)
            from .coordinates import write
            write(work,world)
            c.view_layer.update()
            if bpy.ops.sketchgroup.instance_finish('EXEC_DEFAULT')!={'FINISHED'}:raise ValueError('加入组未通过提交检查')
            committed=True
            target.instance_collection.instance_offset=offset+delta
            if delta.length>1e-8:
                for peer,matrix in peer_world.items():world_parent(peer,peer.parent,matrix@Matrix.Translation(delta))
            c.view_layer.update();f.choose(c,[target]);f.focus.invalidate()
            self.report({'INFO'},'已加入组，关联实例已同步');return {'FINISHED'}
        except Exception as ex:
            if not committed:
                for obj,(cols,parent,inverse,basis) in saved.items():
                    for col in cols:
                        if obj.name not in col.objects:col.objects.link(obj)
                    for col in list(obj.users_collection):
                        if col not in cols:col.objects.unlink(obj)
                    obj.parent=parent;obj.matrix_parent_inverse=inverse;obj.matrix_basis=basis
                if f.session(c) and f.session(c)!=original_work:
                    bpy.ops.sketchgroup.instance_finish('EXEC_DEFAULT')
                f.choose(c,selected)
                if active and active.select_get():c.view_layer.objects.active=active
            self.report({'WARNING'},'加入组失败：'+str(ex));return {'FINISHED'} if committed else {'CANCELLED'}


class SGI_OT_separate(bpy.types.Operator):
    bl_idname='sketchgroup.instance_separate'
    bl_label='分离所选物体到场景'
    bl_description='原位分离到场景，提交并退出全部编辑层；保持所选对象和活动项，可一次撤销'
    bl_options={'REGISTER','UNDO'}
    @classmethod
    def poll(cls,c):
        f=api()
        return c.mode=='OBJECT' and f.session(c) is not None and bool(c.selected_objects) and f.within_scope(c,c.selected_objects)
    @preview.restore_after
    def execute(self,c):
        f=api()
        from . import preview
        preview.clear();c.view_layer.update()
        work=f.session(c);chain=f.session_chain(work);objects,top=selected_tree(c)
        selected=list(c.selected_objects);active=c.active_object
        outer=chain[-1]['sg_session_target'];parent=outer.parent
        from .coordinates import target_collections
        destinations=target_collections(chain[-1]) or [c.scene.collection]
        saved={o:list(o.users_collection) for o in objects}
        transforms={o:(o.parent,o.matrix_parent_inverse.copy(),o.matrix_basis.copy()) for o in top}
        worlds={o:o.matrix_world.copy() for o in top}
        try:
            validate_move(c,objects,top)
            if any(col.library or col.override_library for col in destinations):raise ValueError('场景目标集合不可写')
            if parent:f.safety.matrix_check(parent.matrix_world,'场景父级',invertible=True)
            else:
                from .explode import close_matrix,simple_matrix
                if any(not close_matrix(o.matrix_world,simple_matrix(o.matrix_world)) for o in top):
                    raise ValueError('分离后会产生无法直接表示的剪切变换，请先整理组的非均匀缩放')
        except (ValueError,RuntimeError) as ex:
            preview.refresh(c)
            self.report({'WARNING'},str(ex));return {'CANCELLED'}
        try:
            for obj in objects:
                for col in destinations:
                    if obj.name not in col.objects:col.objects.link(obj)
                for col in saved[obj]:
                    if col not in destinations:col.objects.unlink(obj)
            for obj in top:world_parent(obj,parent,worlds[obj])
            c.view_layer.update()
            for level in chain:
                f.validate_finish(level)
        except Exception as ex:
            for obj,cols in saved.items():
                for col in cols:
                    if obj.name not in col.objects:col.objects.link(obj)
                for col in list(obj.users_collection):
                    if col not in cols:col.objects.unlink(obj)
            for obj,(old_parent,inverse,basis) in transforms.items():
                obj.parent=old_parent;obj.matrix_parent_inverse=inverse;obj.matrix_basis=basis
            c.view_layer.update();f.choose(c,selected)
            if active and active.select_get():c.view_layer.objects.active=active
            f.focus.invalidate();preview.refresh(c)
            self.report({'WARNING'},'分离失败，已恢复：'+str(ex));return {'CANCELLED'}
        try:
            for level in chain:
                if f.session(c)!=level or bpy.ops.sketchgroup.instance_finish('EXEC_DEFAULT')!={'FINISHED'}:
                    raise RuntimeError('某一编辑层未能提交')
        except Exception as ex:
            # Keep the outer undo step if an unexpected error occurs after commits.
            # Returning CANCELLED here would discard recovery for partial changes.
            f.include_local_view(c,selected);f.choose(c,selected)
            if active and active.select_get():c.view_layer.objects.active=active
            self.report({'WARNING'},'分离已执行，但未完整退出：'+str(ex)+'；请撤销一次恢复本次操作')
            return {'FINISHED'}
        f.include_local_view(c,selected);f.choose(c,selected)
        if active and active.select_get():c.view_layer.objects.active=active
        f.focus.invalidate()
        self.report({'INFO'},'已原位分离并退出到场景，保留选中项和活动项')
        return {'FINISHED'}




class SGI_OT_dissolve_current(bpy.types.Operator):
    bl_idname='sketchgroup.dissolve_current'
    bl_label='解散'
    bl_description='解散当前编辑组这一层，全部内容原地移到上一层；保留子组及其他关联实例'
    bl_options={'REGISTER','UNDO'}
    recursive: BoolProperty(name='解散全部',description='同时解散当前组内所有嵌套子组',default=False)
    @classmethod
    def poll(cls,c):return c.mode=='OBJECT' and api().session(c) is not None
    @preview.restore_after
    def execute(self,c):
        from . import preview
        from .explode import build_plan
        f=api();work=f.session(c)
        target=work.get('sg_session_target')
        if not target:
            self.report({'WARNING'},'会话对象缺失，请撤销删除后再解散');return {'CANCELLED'}
        preview.clear();c.view_layer.update()
        try:build_plan(target,work,editing=True)
        except (ValueError,RuntimeError) as ex:
            self.report({'WARNING'},str(ex));return {'CANCELLED'}
        if bpy.ops.sketchgroup.instance_finish('EXEC_DEFAULT')!={'FINISHED'}:return {'CANCELLED'}
        try:
            if bpy.ops.sketchgroup.instance_explode('EXEC_DEFAULT',False,recursive=self.recursive)=={'FINISHED'}:return {'FINISHED'}
        except RuntimeError as ex:self.report({'WARNING'},str(ex))
        self.report({'WARNING'},'编辑已提交，但解散未完成；组已保留，可撤销返回编辑状态')
        return {'FINISHED'}


class SGI_MT_separate(bpy.types.Menu):
    bl_idname='SGI_MT_separate'
    bl_label='分离'
    def draw(self,c):
        editing=api().session(c) is not None
        if editing:self.layout.operator('sketchgroup.instance_separate',text='选中项',icon='EXPORT')
        operator='sketchgroup.dissolve_current' if editing else 'sketchgroup.instance_explode'
        self.layout.operator(operator,text='解散',icon='UNLINKED').recursive=False
        self.layout.operator(operator,text='解散全部',icon='UNLINKED').recursive=True


class SGI_OT_separate_menu(bpy.types.Operator):
    bl_idname='sketchgroup.separate_menu'
    bl_label='分离'
    @classmethod
    def poll(cls,c):
        f=api()
        return c.mode=='OBJECT' and (f.session(c) is not None or f.SGI_OT_explode.poll(c))
    def execute(self,c):return bpy.ops.wm.call_menu(name=SGI_MT_separate.bl_idname)


def tab_action(c):
    if not c.area or c.area.type!='VIEW_3D' or not c.region or c.region.type!='WINDOW' or c.mode!='OBJECT':return None
    f=api()
    if not c.selected_objects:return 'EXIT' if f.session(c) else None
    target=c.active_object
    if f.is_instance(target) and target.select_get() and f.within_scope(c,[target]):return 'ENTER'
    return None


class SGI_OT_tab(bpy.types.Operator):
    bl_idname='sketchgroup.tab_edit'
    bl_label='Tab 编辑组 / 空选退出'
    bl_description='选中活动组时进入；组编辑中空选时提交并退出一层。普通物体和网格编辑保留原生 Tab'
    bl_options={'UNDO','INTERNAL'}
    @classmethod
    def poll(cls,c):return tab_action(c) is not None
    def invoke(self,c,event):
        from . import keymaps
        if not keymaps.accepts(c,'sketchgroup.tab_edit',event):return {'PASS_THROUGH'}
        if getattr(event,'is_repeat',False):return {'CANCELLED'}
        return self.execute(c)
    def execute(self,c):
        action=tab_action(c)
        if action=='ENTER':return bpy.ops.sketchgroup.instance_enter('EXEC_DEFAULT')
        if action=='EXIT':return bpy.ops.sketchgroup.instance_finish('EXEC_DEFAULT')
        return {'CANCELLED'}


class SGI_OT_escape(bpy.types.Operator):
    bl_idname='sketchgroup.escape_exit'
    bl_label='退出当前组'
    bl_description='Esc 退出当前组并保留组选中；进行中的操作和网格编辑保留原生取消行为'
    bl_options={'UNDO','INTERNAL'}
    @classmethod
    def poll(cls,c):
        return (c.area is not None and c.area.type=='VIEW_3D' and c.region is not None
                and c.region.type=='WINDOW' and c.mode=='OBJECT' and api().session(c) is not None)
    def invoke(self,c,event):
        from . import keymaps
        if not keymaps.accepts(c,'sketchgroup.escape_exit',event):return {'PASS_THROUGH'}
        if getattr(event,'is_repeat',False):return {'CANCELLED'}
        return self.execute(c)
    def execute(self,c):return bpy.ops.sketchgroup.instance_finish('EXEC_DEFAULT',False)


CLASSES=(SGI_OT_join,SGI_OT_separate,SGI_OT_dissolve_current,SGI_MT_separate,SGI_OT_separate_menu,SGI_OT_tab,SGI_OT_escape)
