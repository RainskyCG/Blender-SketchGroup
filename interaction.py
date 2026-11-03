"""One native press/drag/release route for leaving a group at the pointer."""
import bpy
from bpy.props import IntProperty
from mathutils import Matrix,Vector

KEYS=[]
_enabled=False
MOUSE_BUTTONS={'LEFTMOUSE','MIDDLEMOUSE','RIGHTMOUSE','BUTTON4MOUSE','BUTTON5MOUSE'}


def window_region(c,event):
    area=c.area
    if not area or area.type!='VIEW_3D' or not c.region:return None
    if c.region.type!='WINDOW':
        if c.region.type not in {'UI','TOOLS'} or not c.preferences.system.use_region_overlap:return None
    # Window keymaps run after native region/UI handlers. An event reaching
    # this route has already passed actual buttons, panels and gizmos.
    for region in area.regions:
        if region.type=='WINDOW' and region.x<=event.mouse_x<region.x+region.width and region.y<=event.mouse_y<region.y+region.height:
            return area,region
    return None


def segment_hits_box(start,end,low,high):
    near,far=0.0,1.0
    direction=end-start
    for axis in range(3):
        if abs(direction[axis])<1e-12:
            if not low[axis]<=start[axis]<=high[axis]:return False
            continue
        a=(low[axis]-start[axis])/direction[axis]
        b=(high[axis]-start[axis])/direction[axis]
        near=max(near,min(a,b));far=min(far,max(a,b))
        if near>far:return False
    return True


def exit_levels(c,x,y):
    from . import focus,instances
    chain=instances.session_chain(instances.session(c))
    if not chain:return 0
    projection=c.region_data.perspective_matrix.inverted_safe()
    rays=[]
    for dx,dy in ((0,0),(-5,0),(5,0),(0,-5),(0,5),(-4,-4),(-4,4),(4,-4),(4,4)):
        points=[projection@Vector((2*(x+dx)/c.region.width-1,2*(y+dy)/c.region.height-1,z,1)) for z in (-1,1)]
        if all(abs(p.w)>1e-12 for p in points):rays.append(tuple(p.to_3d()/p.w for p in points))
    levels=0
    for work in chain:
        from .coordinates import frame
        inverse=frame(work).inverted_safe()
        points=[inverse@p for p in focus.world_points(c,work)]
        low=Vector(tuple(min(p[i] for p in points) for i in range(3)))
        high=Vector(tuple(max(p[i] for p in points) for i in range(3)))
        if any(segment_hits_box(inverse@a,inverse@b,low,high) for a,b in rays):break
        levels+=1
    return levels


def finish_levels(c,levels,report,*,deselect=True):
    previous_mode=c.mode
    if previous_mode.startswith('EDIT'):bpy.ops.object.mode_set('EXEC_DEFAULT',False,mode='OBJECT')
    completed=0
    for _ in range(levels):
        try:result=bpy.ops.sketchgroup.instance_finish('EXEC_DEFAULT',False)
        except RuntimeError as ex:
            report({'WARNING'},str(ex));result={'CANCELLED'}
        if result!={'FINISHED'}:break
        completed+=1
    if completed and completed<levels:
        report({'WARNING'},'已返回可提交的上级组；剩余层存在兼容性问题，请处理后继续退出')
    if not completed and previous_mode.startswith('EDIT') and c.active_object:
        bpy.ops.object.mode_set('EXEC_DEFAULT',False,mode='EDIT')
    if completed and deselect:
        from .instances import choose
        choose(c,[],update=False)
    return {'FINISHED'} if completed else result


class SGI_OT_return_level(bpy.types.Operator):
    bl_idname='sketchgroup.return_level'
    bl_label='返回组层级'
    bl_description='返回此组的编辑层；场景按钮退出全部层级'
    bl_options={'UNDO','INTERNAL'}
    depth: IntProperty(min=0)
    @classmethod
    def poll(cls,c):
        from .instances import session
        return bool(session(c)) and (c.mode=='OBJECT' or c.mode.startswith('EDIT'))
    def execute(self,c):
        from .instances import session,session_chain
        if c.window and any(op.bl_idname.startswith('TRANSFORM_OT_') for op in c.window.modal_operators):return {'CANCELLED'}
        levels=len(session_chain(session(c)))-self.depth
        if levels<=0:return {'CANCELLED'}
        return finish_levels(c,levels,self.report,deselect=False)


class SGI_OT_click_exit(bpy.types.Operator):
    bl_idname='sketchgroup.click_exit'
    bl_label='单击组外退出'
    bl_description='在组框外按下并原地松开后退出；拖拽、导航及框内操作交给 Blender'
    bl_options={'UNDO','INTERNAL'}

    @classmethod
    def poll(cls,c):
        from .instances import session
        return (c.area is not None and c.area.type=='VIEW_3D' and c.region is not None
                and (c.region.type=='WINDOW' or (c.region.type in {'UI','TOOLS'} and c.preferences.system.use_region_overlap))
                and (c.mode=='OBJECT' or c.mode.startswith('EDIT'))
                and session(c) is not None)

    def invoke(self,c,event):
        from . import keymaps
        if event.value!='PRESS' or not keymaps.accepts(c,'sketchgroup.click_exit',event):return {'PASS_THROUGH'}
        hit=window_region(c,event)
        if not hit:return {'PASS_THROUGH'}
        with c.temp_override(area=hit[0],region=hit[1]):
            return self.begin(c,event)

    def begin(self,c,event):
        from . import instances
        levels=exit_levels(c,event.mouse_x-c.region.x,event.mouse_y-c.region.y)
        if not levels:return {'PASS_THROUGH'}
        if event.type not in MOUSE_BUTTONS:return finish_levels(c,levels,self.report)
        self._button=event.type;self._x=event.mouse_x;self._y=event.mouse_y
        self._area=c.area.as_pointer();self._region=c.region.as_pointer()
        self._work=instances.session(c).as_pointer();self._mode=c.mode
        self._view=tuple(v for row in c.region_data.perspective_matrix for v in row)
        self._threshold=max(1,c.preferences.inputs.drag_threshold_mouse)*c.preferences.system.pixel_size
        c.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL','PASS_THROUGH'}

    def modal(self,c,event):
        from . import instances,keymaps
        cancel={'CANCELLED','PASS_THROUGH'}
        work=instances.session(c)
        if not _enabled or not work or work.as_pointer()!=self._work or c.mode!=self._mode:return cancel
        if event.type in {'MOUSEMOVE','INBETWEEN_MOUSEMOVE'}:
            return cancel if max(abs(event.mouse_x-self._x),abs(event.mouse_y-self._y))>self._threshold else {'PASS_THROUGH'}
        if event.type==self._button and event.value=='RELEASE':
            if max(abs(event.mouse_x-self._x),abs(event.mouse_y-self._y))>self._threshold:return cancel
            hit=window_region(c,event)
            if not hit:return cancel
            area,region=hit
            if (area.as_pointer(),region.as_pointer())!=(self._area,self._region):return cancel
            with c.temp_override(area=area,region=region):
                if tuple(v for row in c.region_data.perspective_matrix for v in row)!=self._view:return cancel
                if not keymaps.accepts(c,'sketchgroup.click_exit',event,value='PRESS'):return cancel
                levels=exit_levels(c,event.mouse_x-region.x,event.mouse_y-region.y)
                return finish_levels(c,levels,self.report) if levels else cancel
        if event.type.startswith('TIMER'):return {'PASS_THROUGH'}
        return cancel


def register():
    global _enabled
    bpy.utils.register_class(SGI_OT_return_level)
    bpy.utils.register_class(SGI_OT_click_exit);_enabled=True
    config=bpy.context.window_manager.keyconfigs.addon
    if config:
        km=config.keymaps.new(name='Window',space_type='EMPTY')
        KEYS.append((km,km.keymap_items.new('sketchgroup.click_exit','LEFTMOUSE','PRESS')))


def unregister():
    global _enabled
    _enabled=False
    for km,item in KEYS:km.keymap_items.remove(item)
    KEYS.clear();bpy.utils.unregister_class(SGI_OT_click_exit)
    bpy.utils.unregister_class(SGI_OT_return_level)
