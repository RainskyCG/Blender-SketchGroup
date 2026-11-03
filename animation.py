TRANSFORMS={'location','rotation_euler','rotation_quaternion','rotation_axis_angle','scale','delta_location','delta_rotation_euler','delta_rotation_quaternion','delta_scale'}
STATIC_METADATA={'["sg_center_axes"]','["sg_center_original_size"]'}


def constant_curve(curve):
    from math import isfinite
    if any(not modifier.mute for modifier in curve.modifiers):return False
    values=[point.co.y for point in curve.keyframe_points]+[point.co.y for point in curve.sampled_points]
    if not values:return True
    value=values[0]
    if any(not isfinite(item) or abs(item-value)>1e-7 for item in values):return False
    for point in curve.keyframe_points:
        if any(not isfinite(y) or abs(y-value)>1e-7 for y in (point.handle_left.y,point.handle_right.y)):return False
    return True


def curves(data):
    action=data.action
    if not action:return []
    if getattr(action,'is_action_legacy',False):return list(action.fcurves)
    result=[]
    for layer in action.layers:
        for strip in layer.strips:
            for bag in getattr(strip,'channelbags',()):
                if bag.slot_handle==data.action_slot_handle:result.extend(bag.fcurves)
    return result


def check_chain(obj,allow_animation=False):
    current=obj
    while current:
        if current.constraints or current.parent_type!='OBJECT' or current.rigid_body:
            raise ValueError(current.name+'：约束、骨骼父级或刚体暂不支持组编辑')
        data=current.animation_data
        if data:
            if not allow_animation:raise ValueError(current.name+'：此操作会改变动画载体，暂不支持；可进入组编辑内部模型')
            if data.drivers or data.nla_tracks:raise ValueError(current.name+'：驱动或 NLA 暂未支持，仅支持实例变换关键帧')
            blocked=[]
            for curve in curves(data):
                if curve.data_path in TRANSFORMS:continue
                if curve.data_path in STATIC_METADATA and constant_curve(curve):continue
                blocked.append(curve.data_path)
            if blocked:
                raise ValueError(current.name+'：暂不支持此动画通道 '+', '.join(dict.fromkeys(blocked))+'；固定值的插件内部属性可保留')
        current=current.parent
