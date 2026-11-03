

def hide_helper(obj):
    for prop,value in (('hide_select',True),('hide_render',True),('hide_viewport',False),
                       ('empty_display_type','IMAGE'),('show_empty_image_orthographic',False),
                       ('show_empty_image_perspective',False)):
        if getattr(obj,prop)!=value:setattr(obj,prop,value)
    for scene in obj.users_scene:
        for layer in scene.view_layers:
            if obj.name in layer.objects and not obj.hide_get(view_layer=layer):obj.hide_set(True,view_layer=layer)


def set_center(obj,axes=False):
    obj['sg_center_axes']=axes
    obj.show_instancer_for_viewport=True
    if axes:
        obj.empty_display_type=obj.get('sg_center_original_type','PLAIN_AXES')
        obj.empty_display_size=max(.1,float(obj.get('sg_center_original_size',.35)))
    else:
        if obj.empty_display_type!='IMAGE':
            obj['sg_center_original_type']=obj.empty_display_type
            obj['sg_center_original_size']=obj.empty_display_size
        obj.empty_display_type='IMAGE'
        obj.show_empty_image_orthographic=False
        obj.show_empty_image_perspective=False
