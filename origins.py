import bpy,json
from . import preview
from mathutils import Matrix
from bpy.props import EnumProperty,StringProperty,IntProperty

_running=None
_keys=[]
_signature=None
_native={'transform.translate':'TRANSLATE','transform.rotate':'ROTATE','transform.resize':'SCALE'}


def native_bindings():
    from .keymaps import EVENT_FIELDS
    config=bpy.context.window_manager.keyconfigs.user
    if not config:return []
    result=[]
    for name in ('Object Mode','3D View'):
        km=config.keymaps.get(name)
        if not km:continue
        for item in km.keymap_items:
            if item.idname not in _native or item.map_type!='KEYBOARD':continue
            if getattr(item.properties,'cursor_transform',False):continue
            properties={}
            for key in item.properties.keys():
                value=getattr(item.properties,key)
                if not isinstance(value,(bool,int,float,str)):value=list(value)
                properties[key]=value
            result.append((name,km.space_type,km.region_type,_native[item.idname],
                           tuple(getattr(item,field) for field in EVENT_FIELDS),json.dumps(properties)))
    return result


def sync_bindings():
    global _signature
    if _running:return .5
    from .keymaps import EVENT_FIELDS
    config=bpy.context.window_manager.keyconfigs.addon
    if not config:return 1.0
    bindings=native_bindings()
    signature=tuple(bindings)
    if signature!=_signature:
        while len(_keys)>len(bindings):
            km,item=_keys.pop();km.keymap_items.remove(item)
        for index,(name,space,region,mode,values,options) in enumerate(bindings):
            fields=dict(zip(EVENT_FIELDS,values))
            if index<len(_keys) and _keys[index][0].name!=name:
                old_map,old_item=_keys[index];old_map.keymap_items.remove(old_item)
                km=config.keymaps.new(name=name,space_type=space,region_type=region)
                item=km.keymap_items.new('sketchgroup.origin_transform',fields['type'],fields['value'])
                _keys[index]=(km,item)
            elif index<len(_keys):km,item=_keys[index]
            else:
                km=config.keymaps.new(name=name,space_type=space,region_type=region)
                item=km.keymap_items.new('sketchgroup.origin_transform',fields['type'],fields['value'])
                _keys.append((km,item))
            for key,value in fields.items():setattr(item,key,value)
            item.properties.mode=mode;item.properties.native_options=options;item.properties.route=index
        _signature=signature
        bpy.context.window_manager.keyconfigs.update(keep_properties=True)
        user=bpy.context.window_manager.keyconfigs.user
        for index,(name,space,region,mode,values,options) in enumerate(bindings):
            km=user.keymaps.get(name)
            if not km:continue
            for item in km.keymap_items:
                if item.idname!='sketchgroup.origin_transform' or item.properties.route!=index:continue
                for key,value in zip(EVENT_FIELDS,values):
                    if getattr(item,key)!=value:setattr(item,key,value)
                item.properties.mode=mode;item.properties.native_options=options
    return 1.0


class OriginEdit:
    @preview.restore_after
    def __init__(self,c):
        from . import instances as f,preview,safety,coordinates
        self.c=c;self.ledger=[];self.work=None;self.done=False
        preview.clear();c.view_layer.update()
        selected=list(c.selected_objects)
        if len(selected)!=1 or not f.is_instance(c.active_object) or c.active_object!=selected[0]:
            raise ValueError('调整组原点时请只选择一个组；关联组件会同步调整中心')
        self.target=selected[0];f.check_instance(self.target)
        if not f.within_scope(c,selected):raise ValueError('只能调整当前编辑层内的组')
        self.source=self.target.instance_collection;self.parent_work=f.session(c)
        peers=[o for o in bpy.data.objects if o.instance_collection==self.source]
        parents={o.parent for o in bpy.data.objects if o.parent}
        for obj in peers:
            f.check_instance(obj)
            if obj in parents:raise ValueError(obj.name+'：含外部子对象，暂不能安全调整组原点')
            if obj.parent in peers:raise ValueError('关联实例互为父子，暂不能同时调整原点')
        cols,objects=f.validate_source(self.source,deep=False)
        safety.check_incoming(objects,cols)
        if any(set(cols).intersection(scene.collection.children_recursive) for scene in bpy.data.scenes):
            raise ValueError('源集合本身位于场景中，请先独立化此组再调整原点，以保留源物体位置')
        self.start=self.target.matrix_world.copy()
        self.saved={o:(o.instance_collection,o.parent,o.matrix_parent_inverse.copy(),o.matrix_basis.copy(),o.matrix_world.copy()) for o in peers}
        try:
            self.work=f.clone_definition(self.source,deep=False,ledger=self.ledger)
            self.work.name=self.source.name+' · 原点调整'
            roots,_=coordinates.roots(self.work)
            world=coordinates.matrices(roots)
            self.roots={obj:world[obj] for obj in roots}
            for obj in peers:obj.instance_collection=self.work
            self.offset=Matrix.Translation(self.source.instance_offset)
            self.last=self.start.copy()
            c.view_layer.update()
        except Exception:
            self.cancel();raise

    def apply(self,desired):
        from . import safety,focus,structure
        from .explode import close_matrix,simple_matrix
        safety.matrix_check(desired,'组原点',invertible=True)
        change=self.start.inverted()@desired
        matrices={obj:saved[4]@change for obj,saved in self.saved.items()}
        for obj,matrix in matrices.items():
            safety.matrix_check(matrix,obj.name,invertible=True)
            if obj.parent is None and not close_matrix(matrix,simple_matrix(matrix)):
                raise ValueError(obj.name+'：调整后会产生无法无损表示的剪切，已保留原模型')
        rebase=self.offset@desired.inverted()@self.start@self.offset.inverted()
        from . import coordinates
        coordinates.write(self.work,{obj:rebase@matrix for obj,matrix in self.roots.items()},self.ledger)
        for obj,matrix in matrices.items():structure.world_parent(obj,obj.parent,matrix)
        self.c.view_layer.update();focus.invalidate();self.last=desired.copy()
        if self.parent_work:preview.refresh(self.c,True)

    def cancel(self):
        if self.done:return
        from . import safety,focus
        for obj,(source,parent,inverse,basis,world) in self.saved.items():
            obj.instance_collection=source;obj.parent=parent;obj.matrix_parent_inverse=inverse;obj.matrix_basis=basis
        safety.rollback(self.ledger);self.ledger=[];self.done=True
        self.c.view_layer.update();focus.invalidate()
        if self.parent_work:preview.refresh(self.c,True)

    def commit(self):
        if self.done:return
        from . import coordinates
        coordinates.tidy(coordinates.roots(self.work)[1])
        for obj in list(self.source.objects):self.source.objects.unlink(obj)
        for col in list(self.source.children):self.source.children.unlink(col)
        for obj in list(self.work.objects):
            self.source.objects.link(obj);self.work.objects.unlink(obj)
        for col in list(self.work.children):
            self.source.children.link(col);self.work.children.unlink(col)
        for obj in self.saved:obj.instance_collection=self.source
        bpy.data.collections.remove(self.work)
        self.ledger=[];self.done=True
        self.c.view_layer.update()
        if self.parent_work:preview.refresh(self.c,True)


class SGI_OT_origin_transform(bpy.types.Operator):
    bl_idname='sketchgroup.origin_transform'
    bl_label='变换组原点'
    bl_description='仅变换组原点，模型保持原位；关联组件同步中心，Esc 取消'
    bl_options={'UNDO','BLOCKING'}
    mode: EnumProperty(items=(('TRANSLATE','移动',''),('ROTATE','旋转',''),('SCALE','缩放','')),default='TRANSLATE')
    native_options: StringProperty(default='{}',options={'HIDDEN','SKIP_SAVE'})
    route: IntProperty(default=0,options={'HIDDEN'})
    @classmethod
    def poll(cls,c):
        from .instances import is_instance
        return bool(c.area and c.area.type=='VIEW_3D' and c.region and c.region.type=='WINDOW' and c.mode=='OBJECT' and c.scene.tool_settings.use_transform_data_origin and any(is_instance(o) for o in c.selected_objects))
    def invoke(self,c,event):
        global _running
        if _running:return {'CANCELLED'}
        self._edit=None;self._proxy=None;self._timer=None;self._cancel=False;self._finished=False
        self._origin_option=c.scene.tool_settings.use_transform_data_origin
        self._global_undo=c.preferences.edit.use_global_undo
        try:
            self._edit=OriginEdit(c)
            self._proxy=bpy.data.objects.new('SG · 调整原点',None)
            self._proxy['sg_origin_proxy']=True;self._proxy.hide_render=True
            self._proxy.empty_display_type='ARROWS';self._proxy.empty_display_size=.35
            c.collection.objects.link(self._proxy)
            target=self._edit.target
            self._proxy.rotation_mode=target.rotation_mode
            self._proxy.parent=target.parent;self._proxy.matrix_parent_inverse=target.matrix_parent_inverse.copy();self._proxy.matrix_basis=target.matrix_basis.copy()
            for prop in ('lock_location','lock_rotation','lock_scale','lock_rotation_w','lock_rotations_4d'):
                setattr(self._proxy,prop,getattr(target,prop))
            from .instances import choose
            choose(c,[self._proxy])
            c.scene.tool_settings.use_transform_data_origin=False
            # The outer operator owns the single undo step; native modal completion
            # must not record the temporary proxy and private working definition.
            c.preferences.edit.use_global_undo=False
            native={'TRANSLATE':bpy.ops.transform.translate,'ROTATE':bpy.ops.transform.rotate,'SCALE':bpy.ops.transform.resize}[self.mode]
            result=native('INVOKE_DEFAULT',**json.loads(self.native_options))
            if 'RUNNING_MODAL' not in result:raise ValueError('无法启动原点变换')
            self._timer=c.window_manager.event_timer_add(.01,window=c.window)
            c.window_manager.modal_handler_add(self);_running=self
            return {'RUNNING_MODAL'}
        except Exception as ex:
            self.report({'WARNING'},str(ex));self.finish(c,True);return {'CANCELLED'}
    def modal(self,c,event):
        if event.type in {'ESC','RIGHTMOUSE'} and event.value=='PRESS':self._cancel=True
        if event.type!='TIMER':return {'PASS_THROUGH'}
        active=any(op.bl_idname.startswith('TRANSFORM_OT_') for op in c.window.modal_operators)
        try:
            desired=self._proxy.matrix_world.copy()
            if desired!=self._edit.last:self._edit.apply(desired)
            error=None
        except (ValueError,RuntimeError,ReferenceError) as ex:error=str(ex)
        if active:return {'PASS_THROUGH'}
        if error:self.report({'WARNING'},error)
        from .explode import close_matrix
        cancel=self._cancel or bool(error) or close_matrix(self._proxy.matrix_world,self._edit.start)
        self.finish(c,cancel)
        return {'CANCELLED'} if cancel else {'FINISHED'}
    def finish(self,c,cancel):
        global _running
        if self._finished:return
        self._finished=True
        from . import instances as f,preview
        try:
            if self._edit:
                if cancel:self._edit.cancel()
                else:self._edit.commit()
        finally:
            c.preferences.edit.use_global_undo=self._global_undo
            c.scene.tool_settings.use_transform_data_origin=self._origin_option
            if self._timer:c.window_manager.event_timer_remove(self._timer);self._timer=None
            if self._proxy:bpy.data.objects.remove(self._proxy,do_unlink=True);self._proxy=None
            if self._edit:f.choose(c,[self._edit.target])
            _running=None;preview.refresh(c)
    def cancel(self,c):self.finish(c,True)


def register():
    bpy.utils.register_class(SGI_OT_origin_transform)
    bpy.app.timers.register(sync_bindings,first_interval=.2)


def unregister():
    global _signature
    if bpy.app.timers.is_registered(sync_bindings):bpy.app.timers.unregister(sync_bindings)
    if _running:_running.finish(bpy.context,True)
    for km,item in _keys:km.keymap_items.remove(item)
    _keys.clear();_signature=None
    bpy.utils.unregister_class(SGI_OT_origin_transform)
