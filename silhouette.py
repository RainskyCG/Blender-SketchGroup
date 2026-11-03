import bpy
from array import array
from bpy.app.handlers import persistent
from .draw_dependencies import Dependencies,updates

_handle=None
_enabled=False
_suspended=False
_programs=None
_textures={}
_draws={}
_mask_draws={}
_outline_draws={}
_error=''
_revision=0
_frame_pending=False
_stats={}


def programs():
    global _programs
    if _programs:return _programs
    import gpu
    from gpu_extras.batch import batch_for_shader
    if gpu.platform.backend_type_get()!='OPENGL':
        raise RuntimeError('当前图形后端暂不支持轮廓遮罩，请使用正常显示')
    info=gpu.types.GPUShaderCreateInfo()
    info.typedef_source('struct SGTransforms { mat4 model[64]; };')
    info.uniform_buf(0,'SGTransforms','transforms')
    info.push_constant('MAT4','viewProjection')
    info.push_constant('INT','maskBit')
    info.push_constant('VEC2','viewportOrigin')
    info.image(0,'R32UI','UINT_2D','visibleMask',qualifiers={'READ','WRITE'})
    info.vertex_in(0,'VEC3','pos')
    info.fragment_out(0,'VEC4','fragColor')
    info.vertex_source('void main(){ gl_Position=viewProjection*transforms.model[gl_InstanceID]*vec4(pos,1.0); gl_Position.z-=0.0000002*gl_Position.w; }')
    info.fragment_source('''layout(early_fragment_tests) in;
void main(){
    ivec2 pixel=ivec2(gl_FragCoord.xy-viewportOrigin);
    if(all(greaterThanEqual(pixel,ivec2(0))) && all(lessThan(pixel,imageSize(visibleMask))))
        imageAtomicOr(visibleMask,pixel,uint(maskBit));
    fragColor=vec4(0.0);
}''')
    mark=gpu.shader.create_from_info(info)
    info=gpu.types.GPUShaderCreateInfo()
    info.sampler(0,'UINT_2D','visibleMask')
    info.push_constant('VEC2','viewportOrigin')
    info.push_constant('FLOAT','strength')
    info.push_constant('VEC3','maskColor')
    info.push_constant('VEC3','outlineColor')
    info.push_constant('VEC3','selectedColor')
    info.push_constant('BOOL','thickOutline')
    info.push_constant('BOOL','smoothOutline')
    info.push_constant('BOOL','drawOutline')
    info.vertex_in(0,'VEC2','pos')
    info.fragment_out(0,'VEC4','fragColor')
    info.vertex_source('void main(){ gl_Position=vec4(pos,0.0,1.0); }')
    info.fragment_source('''uint bitsAt(ivec2 p){
    ivec2 size=textureSize(visibleMask,0);
    if(any(lessThan(p,ivec2(0))) || any(greaterThanEqual(p,size))) return 0u;
    return texelFetch(visibleMask,p,0).r;
}
void main(){
    ivec2 pixel=ivec2(gl_FragCoord.xy-viewportOrigin);
    uint bits=bitsAt(pixel);
    float opacity=strength*float((bits&1u)==0u);
    float coverage=0.0;
    uint outlineBits=bits;
    if(drawOutline && (bits&1u)==0u){
        bool inside=(bits&2u)!=0u;
        for(int radius=1;radius<=2;radius++){
            if(radius==2 && (!thickOutline || coverage>0.0)) break;
            uvec4 neighbours=uvec4(bitsAt(pixel+ivec2(radius,0)),bitsAt(pixel+ivec2(-radius,0)),
                                  bitsAt(pixel+ivec2(0,radius)),bitsAt(pixel+ivec2(0,-radius)));
            bvec4 occupied=notEqual(neighbours&uvec4(2u),uvec4(0u));
            if(any(notEqual(occupied,bvec4(inside)))){
                vec4 values=vec4(occupied);
                float slope=length(vec2(values.x-values.y,values.z-values.w));
                coverage=smoothOutline ? 1.0/max(1.0,slope) : 1.0;
                if(!inside) outlineBits=neighbours.x|neighbours.y|neighbours.z|neighbours.w;
            }
        }
    }
    coverage*=254.0/255.0;
    vec3 lineColor=(outlineBits&4u)!=0u ? outlineColor : selectedColor;
    float alpha=opacity+coverage*(1.0-opacity);
    vec3 premultiplied=maskColor*opacity+lineColor*coverage*(1.0-opacity);
    fragColor=vec4(alpha>0.0 ? premultiplied/alpha : vec3(0.0),alpha);
}''')
    cover=gpu.shader.create_from_info(info)
    screen=batch_for_shader(cover,'TRIS',{'pos':((-1,-1),(3,-1),(-1,3))})
    # Blender 5.0 exposes no standalone memory barrier. dispatch() inserts both
    # image-access and texture-fetch barriers after this one-invocation no-op.
    info=gpu.types.GPUShaderCreateInfo();info.local_group_size(1,1,1)
    info.compute_source('void main(){}')
    barrier=gpu.shader.create_from_info(info)
    _programs=(mark,cover,screen,barrier)
    return _programs


def paint(commands,matrix,strength,key,color=(0,0,0),outline=(1,.55,.15),show_outline=True,*,selected_outline=None,thick=False,smooth=True):
    import gpu
    mark,cover,screen,barrier=programs()
    viewport=gpu.state.viewport_get();width,height=viewport[2:]
    if width<=0 or height<=0:return
    mask=_textures.get(key)
    if mask is None or (mask.width,mask.height)!=(width,height):
        mask=gpu.types.GPUTexture((width,height),format='R32UI');_textures[key]=mask
    mask.clear(format='UINT',value=(0,))
    depth=gpu.state.depth_test_get();blend=gpu.state.blend_get();write=gpu.state.depth_mask_get()
    try:
        gpu.state.depth_test_set('LESS_EQUAL');gpu.state.depth_mask_set(False)
        # Disabling every write enables rasterizer discard in Blender's OpenGL
        # backend. Keep alpha writes with zero-alpha blending: color and depth
        # remain unchanged, but visible fragments can write the storage image.
        gpu.state.blend_set('ALPHA');gpu.state.color_mask_set(False,False,False,True)
        mark.bind();mark.uniform_float('viewProjection',matrix)
        mark.uniform_float('viewportOrigin',viewport[:2]);mark.image('visibleMask',mask)
        for command in commands:
            geometry,transforms,count=command[:3]
            bit=command[3] if len(command)>3 else 1
            mark.uniform_int('maskBit',bit)
            mark.uniform_block('transforms',transforms)
            geometry.draw_instanced(mark,instance_count=count)
        gpu.compute.dispatch(barrier,1,1,1)
        gpu.state.color_mask_set(True,True,True,True)
        gpu.state.depth_test_set('NONE');gpu.state.blend_set('ALPHA')
        cover.bind();cover.uniform_sampler('visibleMask',mask)
        cover.uniform_float('viewportOrigin',viewport[:2]);cover.uniform_float('strength',strength)
        cover.uniform_float('maskColor',color)
        cover.uniform_float('outlineColor',outline)
        cover.uniform_float('selectedColor',outline if selected_outline is None else selected_outline)
        cover.uniform_bool('thickOutline',thick);cover.uniform_bool('smoothOutline',smooth)
        cover.uniform_bool('drawOutline',show_outline and any(len(cmd)>3 and cmd[3]&2 for cmd in commands))
        screen.draw(cover)
    finally:
        gpu.state.color_mask_set(True,True,True,True)
        gpu.state.depth_mask_set(write);gpu.state.depth_test_set(depth);gpu.state.blend_set(blend)


def collect(c,graph,selected=None,dependencies=None,part='ALL'):
    import gpu
    from . import instances as f,outside,preview
    from mathutils import Matrix
    groups={};visited=set();peer_visited=set()
    dependencies=dependencies if dependencies is not None else Dependencies(c)
    selected=selected if selected is not None else selection(c)
    active=c.view_layer.objects.active
    active_key=active.as_pointer() if active and active.as_pointer() in selected else 0
    selection_paths={}
    def contains_selected(col,path=frozenset()):
        key=col.as_pointer()
        if key in selection_paths:return selection_paths[key]
        if key in path:return False
        dependencies.collection(col)
        result=False
        for obj in col.objects:
            if obj.as_pointer() in selected:result=True;break
            if obj.instance_type=='COLLECTION' and obj.instance_collection:
                dependencies.object(obj)
                if contains_selected(obj.instance_collection,path|{key}):result=True;break
        if not result:result=any(contains_selected(child,path|{key}) for child in col.children)
        selection_paths[key]=result
        return result
    def visit(obj,base,bit,instanced=False,path=frozenset(),selected_parent=False,active_parent=False):
        dependencies.object(obj,visible=not instanced)
        if not instanced and (obj.hide_get(view_layer=c.view_layer) or not obj.visible_in_viewport_get(c.space_data)):return
        if obj.hide_viewport:return
        chosen=selected_parent or obj.as_pointer() in selected
        is_active=active_parent or (chosen and obj.as_pointer()==active_key)
        col=obj.instance_collection if obj.instance_type=='COLLECTION' else None
        if bit==2 and not chosen and (not col or not contains_selected(col)):return
        evaluated=obj.evaluated_get(graph)
        world=base@evaluated.matrix_world
        if col:
            dependencies.collection(col)
            if col.as_pointer() in path or col.hide_viewport:return
            transform=world@Matrix.Translation(-col.instance_offset)
            members=set()
            def gather(collection):
                dependencies.collection(collection)
                if collection.hide_viewport:return
                members.update(collection.objects)
                for child in collection.children:gather(child)
            gather(col)
            for child in members:visit(child,transform,bit,True,path|{col.as_pointer()},chosen,is_active)
        if bit==2 and not chosen:return
        if obj.type not in outside.SURFACES or obj.display_type in {'WIRE','BOUNDS'} or obj.show_in_front:return
        mask_bit=bit|4 if bit==2 and is_active else bit
        key=(obj.as_pointer(),mask_bit)
        if key not in groups:groups[key]=[evaluated,[]]
        groups[key][1].append(world)
        (visited if bit==1 else peer_visited).add(obj.as_pointer())
    if part!='OUTLINE' and c.scene.sg_outside_display=='MASK' and c.scene.sg_mask_rgba[3]>0:
        for col in f.collection_tree(f.session(c)):dependencies.collection(col)
        for obj in f.scope_objects(f.session(c)):visit(obj,Matrix.Identity(4),1)
    if part!='MASK' and selected and c.space_data.overlay.show_outline_selected:
        for peer in tuple(preview._outline_roots):
            try:visit(peer,Matrix.Identity(4),2)
            except ReferenceError:preview.mark()
    commands=[]
    for (key,bit),(obj,matrices) in groups.items():
        geometry=outside.batch(obj,graph.as_pointer(),visible_only=True)
        if geometry is None:continue
        for start in range(0,len(matrices),outside.CHUNK):
            chunk=matrices[start:start+outside.CHUNK]
            values=array('f',(v for matrix in chunk for column in matrix.transposed() for v in column))
            values.extend([0.0]*((outside.CHUNK-len(chunk))*16))
            commands.append((geometry,gpu.types.GPUUniformBuf(values),len(chunk),bit))
    _stats.update(objects=len(visited),peer_objects=len(peer_visited),draw_calls=len(commands),
        instances=sum(len(entry[1]) for entry in groups.values()),
        peer_instances=sum(len(entry[1]) for (key,bit),entry in groups.items() if bit&2))
    return commands


def cached_part(cache,key,signature,c,graph,selected,part):
    entry=cache.get(key)
    if not entry or entry[0]!=signature:
        dependencies=Dependencies(c)
        commands=collect(c,graph,selected,dependencies,part)
        entry=(signature,commands,dependencies,dict(_stats));cache[key]=entry
    return entry


def selection(c):
    from . import instances as f
    work=f.session(c)
    return frozenset(obj.as_pointer() for obj in f.scope_objects(work) if obj.select_get(view_layer=c.view_layer)) if work else frozenset()


def theme_linear(color):
    # Theme values are sRGB; POST_VIEW blends into the linear viewport buffer.
    return tuple(v/12.92 if v<=.04045 else ((v+.055)/1.055)**2.4 for v in color)


def draw():
    global _error,_frame_pending
    if not _enabled or _suspended or _error:return
    from . import instances as f,outside,preview
    c=bpy.context;work=f.session(c)
    if not work or not c.space_data.overlay.show_overlays:return
    if not outside.supported(c) or c.region_data.use_clip_planes:return
    try:
        if _frame_pending:
            _draws.clear();_mask_draws.clear();_outline_draws.clear();_frame_pending=False
        graph=c.evaluated_depsgraph_get()
        key=(c.window.as_pointer(),c.region.as_pointer())
        selected=selection(c)
        active=c.view_layer.objects.active
        active_key=active.as_pointer() if active and active.as_pointer() in selected else 0
        peers=tuple(obj.as_pointer() for obj in preview._outline_roots)
        signature=(_revision,work.as_pointer(),graph.as_pointer(),c.space_data.shading.type,c.space_data.local_view is not None)
        mask=cached_part(_mask_draws,key,signature+(c.scene.sg_outside_display,c.scene.sg_mask_rgba[3]>0),c,graph,frozenset(),'MASK')
        outline=cached_part(_outline_draws,key,signature+(selected,active_key,peers,c.space_data.overlay.show_outline_selected),c,graph,selected,'OUTLINE')
        entry=_draws.get(key)
        if not entry or entry[0][0] is not mask or entry[0][1] is not outline:
            entry=((mask,outline),mask[1]+outline[1]);_draws[key]=entry
        _stats.update(objects=mask[3]['objects'],peer_objects=outline[3]['peer_objects'],draw_calls=len(entry[1]),
                      instances=sum(cmd[2] for cmd in entry[1]),peer_instances=sum(cmd[2] for cmd in outline[1]))
        if not entry[1]:return
        strength=c.scene.sg_mask_rgba[3] if c.scene.sg_outside_display=='MASK' else 0
        theme=c.preferences.themes[0].view_3d
        # Match Blender 5.0's native outline expansion threshold without another pass.
        system=c.preferences.system
        paint(entry[1],c.region_data.perspective_matrix,strength,key,c.scene.sg_mask_rgba[:3],theme_linear(theme.object_active),c.space_data.overlay.show_outline_selected,
              selected_outline=theme_linear(theme.object_selected),thick=system.pixel_size>1 or theme.outline_width>2,
              smooth=system.use_overlay_smooth_wire)
    except Exception as ex:
        _error=str(ex);print('SketchGroup 轮廓遮罩暂停：',_error)


@persistent
def changed(scene,graph):
    if not _enabled or _suspended or not scene.get('sg_active_instance_session'):return
    from . import outside
    ids,scene_changed=updates(graph)
    for cache in (_mask_draws,_outline_draws):
        for key,entry in tuple(cache.items()):
            if entry[2].affected(ids,scene_changed):cache.pop(key,None)
    for key in tuple(_draws):
        if key not in _mask_draws or key not in _outline_draws:_draws.pop(key,None)
    changed_ids={u.id.original.as_pointer() for u in graph.updates if u.is_updated_geometry}
    for key,entry in list(outside._batches.items()):
        if key[1] in changed_ids or entry[1] in changed_ids:outside._batches.pop(key,None)


@persistent
def frame_changed(scene,graph=None):
    global _frame_pending,_revision
    if _enabled and not _suspended and scene.get('sg_active_instance_session'):
        _frame_pending=True;_revision+=1


def invalidate():
    global _revision,_error
    _revision+=1;_error='';_draws.clear();_mask_draws.clear();_outline_draws.clear()


@persistent
def suspend(*args):
    global _suspended
    from . import outside
    _suspended=True;_draws.clear();_mask_draws.clear();_outline_draws.clear();_textures.clear()
    outside._batches.clear()


@persistent
def resume(*args):
    global _suspended
    _suspended=False;invalidate()


def tick():
    if not _enabled:return None
    if not any(s.get('sg_active_instance_session') for s in bpy.data.scenes):
        from . import outside
        _textures.clear();_draws.clear();_mask_draws.clear();_outline_draws.clear()
        outside._batches.clear()
    else:
        live={(w.as_pointer(),r.as_pointer()) for w in bpy.context.window_manager.windows for a in w.screen.areas if a.type=='VIEW_3D' for r in a.regions if r.type=='WINDOW'}
        for cache in (_textures,_draws,_mask_draws,_outline_draws):
            for key in list(cache):
                if key not in live:cache.pop(key,None)
    return 1.0


def handlers():
    return ((bpy.app.handlers.depsgraph_update_post,changed),(bpy.app.handlers.frame_change_post,frame_changed),
        (bpy.app.handlers.load_pre,suspend),(bpy.app.handlers.load_post,resume),
        (bpy.app.handlers.undo_pre,suspend),(bpy.app.handlers.undo_post,resume),
        (bpy.app.handlers.redo_pre,suspend),(bpy.app.handlers.redo_post,resume))


def register():
    global _enabled,_suspended,_handle
    _enabled=True;_suspended=False
    for items,callback in handlers():items.append(callback)
    if not bpy.app.background:
        _handle=bpy.types.SpaceView3D.draw_handler_add(draw,(),'WINDOW','POST_VIEW')
        bpy.app.timers.register(tick,first_interval=1)


def unregister():
    global _enabled,_handle,_programs
    _enabled=False;suspend()
    if _handle is not None:bpy.types.SpaceView3D.draw_handler_remove(_handle,'WINDOW');_handle=None
    if bpy.app.timers.is_registered(tick):bpy.app.timers.unregister(tick)
    for items,callback in handlers():
        if callback in items:items.remove(callback)
    _programs=None
