import bpy
from mathutils import Vector, Matrix
from bpy.app.handlers import persistent
from .draw_dependencies import Dependencies,updates

_handle = None
_wire_handle = None
_wire_shader = None
_projection_cache = {}
_cache = {}
_definition_cache = {}
_selected_cache = {}
_definition_dependencies = {}
_world_dependencies = {}
_wire_batches = {}

def active(c):
    from .instances import session
    return session(c)

def corners(points):
    if not points:return []
    lo=[min(p[i] for p in points) for i in range(3)]
    hi=[max(p[i] for p in points) for i in range(3)]
    return [Vector((x,y,z)) for x in (lo[0],hi[0]) for y in (lo[1],hi[1]) for z in (lo[2],hi[2])]

@persistent
def invalidate(*args):
    _cache.clear();_definition_cache.clear();_projection_cache.clear();_selected_cache.clear()
    _definition_dependencies.clear();_world_dependencies.clear();_wire_batches.clear()


@persistent
def changed(scene,graph):
    if not (_definition_dependencies or _world_dependencies or _selected_cache):return
    ids,scene_changed=updates(graph)
    for key,dependencies in tuple(_definition_dependencies.items()):
        if dependencies.affected(ids,scene_changed):
            _definition_dependencies.pop(key,None);_definition_cache.pop(key,None)
    for key,dependencies in tuple(_world_dependencies.items()):
        if dependencies.affected(ids,scene_changed):
            _world_dependencies.pop(key,None);_cache.pop(key,None)
    for key,entry in tuple(_selected_cache.items()):
        if entry[2].affected(ids,scene_changed):_selected_cache.pop(key,None)

def definition_bounds(col,depsgraph,path):
    key=(col.as_pointer(),depsgraph.as_pointer())
    if key in _definition_cache:return _definition_cache[key]
    if col in path:return []
    points=[];dependencies=Dependencies()
    from .instances import collection_tree
    for child in collection_tree(col):dependencies.collection(child)
    for member in col.all_objects:
        dependencies.object(member)
        if not member.hide_viewport:
            points.extend(object_points(member,member.evaluated_get(depsgraph).matrix_world,depsgraph,path+(col,),dependencies))
    result=corners(points)
    _definition_cache[key]=result
    _definition_dependencies[key]=dependencies
    return result

def object_points(o,matrix,depsgraph,path=(),dependencies=None):
    if o.get('sg_coordinate_anchor') or o.get('sg_origin_proxy'):return []
    if o.instance_type=='COLLECTION' and o.instance_collection:
        col=o.instance_collection
        transform=matrix @ Matrix.Translation(-col.instance_offset)
        result=[transform@p for p in definition_bounds(col,depsgraph,path)]
        child=_definition_dependencies.get((col.as_pointer(),depsgraph.as_pointer()))
        if dependencies is not None and child:dependencies.merge(child)
        return result
    if o.type=='EMPTY':return [matrix.translation.copy()]
    return [matrix@Vector(v) for v in o.evaluated_get(depsgraph).bound_box]

def world_key(c,work):
    return (work.as_pointer(),c.view_layer.as_pointer(),c.space_data.as_pointer() if c.space_data else 0)


def world_points(c,work):
    key=world_key(c,work)
    if key in _cache:return _cache[key]
    graph=c.evaluated_depsgraph_get()
    from . import coordinates
    anchor=work.get('sg_session_target')
    transform=coordinates.frame(work)
    inverse=transform.inverted_safe()
    points=[];dependencies=Dependencies(c)
    if anchor:dependencies.object(anchor)
    from .instances import session_chain,scope_objects,collection_tree
    for col in collection_tree(work):dependencies.collection(col)
    opened={w.get('sg_session_target'):w for w in session_chain(active(c)) if w!=work}
    for o in scope_objects(work):
        dependencies.object(o,visible=True)
        if o.name in c.view_layer.objects and not o.hide_get() and not o.hide_viewport:
            points.extend(inverse@p for p in object_points(o,o.matrix_world,graph,dependencies=dependencies))
    for child in opened.values():
        if child.get('sg_session_parent')==work:
            points.extend(inverse@p for p in world_points(c,child))
            dependencies.merge(_world_dependencies[world_key(c,child)])
    if not points:points=[Vector((0,0,0))]
    result=[transform@p for p in corners(points)]
    _cache[key]=result
    _world_dependencies[key]=dependencies
    return result

EDGES = ((0,1),(0,2),(0,4),(1,3),(1,5),(2,3),(2,6),(3,7),(4,5),(4,6),(5,7),(6,7))


def clip_edge(a,b,matrix):
    ca=matrix@a.to_4d();cb=matrix@b.to_4d()
    low,high=0.0,1.0
    for axis,sign in ((0,1),(0,-1),(1,1),(1,-1),(2,1),(2,-1)):
        fa=ca.w+sign*ca[axis];fb=cb.w+sign*cb[axis]
        if fa<0 and fb<0:return None
        if (fa<0)!=(fb<0):
            t=fa/(fa-fb)
            if fa<0:low=max(low,t)
            else:high=min(high,t)
    if low>high:return None
    a1=a.lerp(b,low);b1=a.lerp(b,high)
    ca=matrix@a1.to_4d();cb=matrix@b1.to_4d()
    if min(ca.w,cb.w)<=1e-8:return None
    return a1,b1,ca,cb


def projected_box(c,work=None):
    work=work if work is not None else active(c)
    if not work or not c.region_data or c.region.type!='WINDOW':return []
    points=world_points(c,work);matrix=c.region_data.perspective_matrix
    w,h=c.region.width,c.region.height
    key=(tuple(v for row in matrix for v in row),w,h)
    region=(c.region.as_pointer(),work.as_pointer())
    cached=_projection_cache.get(region)
    if cached and cached[0] is points and cached[1]==key:return cached[2]
    result=project_points(points,matrix,w,h)
    if region not in _projection_cache and len(_projection_cache)>=128:_projection_cache.clear()
    _projection_cache[region]=(points,key,result)
    return result


def project_points(points,matrix,w,h):
    edges=[]
    for i,j in EDGES:
        clipped=clip_edge(points[i],points[j],matrix)
        if not clipped:continue
        a,b,ca,cb=clipped
        pa=Vector(((ca.x/ca.w+1)*w*.5,(ca.y/ca.w+1)*h*.5))
        pb=Vector(((cb.x/cb.w+1)*w*.5,(cb.y/cb.w+1)*h*.5))
        edges.append((a,b,ca.w,cb.w,(pb-pa).length))
    return edges


def wire_segments(edges,dash=4,gap=5):
    vertices=[];period=dash+gap
    for a,b,wa,wb,length in edges:
        if length<.1:continue
        start=0
        while start<length:
            lo=max(0,start);hi=min(length,start+dash)
            if hi>lo:
                for distance in (lo,hi):
                    fraction=distance/length
                    t=fraction*wa/((1-fraction)*wb+fraction*wa)
                    vertices.append(a.lerp(b,t))
            start+=period
    return vertices


def selected_vertices(c):
    addon=c.preferences.addons.get(__package__)
    style=addon.preferences.selected_bounds_style if addon else 'SOLID'
    if style=='NONE':return []
    if c.mode!='OBJECT':return []
    if c.space_data and c.space_data.type=='VIEW_3D' and not c.space_data.overlay.show_overlays:return []
    objects=[o for o in c.selected_objects if o.instance_type=='COLLECTION' and o.instance_collection]
    if not objects:return []
    if style=='DASHED' and not c.region_data:return []
    graph=c.evaluated_depsgraph_get();vertices=[]
    projection=c.region_data.perspective_matrix if style=='DASHED' else None
    key=(c.window.as_pointer() if c.window else 0,c.region.as_pointer() if c.region else 0)
    signature=(style,graph.as_pointer(),tuple(o.as_pointer() for o in objects),
               tuple(v for row in projection for v in row) if projection is not None else None,
               c.region.width if c.region else 0,c.region.height if c.region else 0,
               c.space_data.local_view.as_pointer() if c.space_data and c.space_data.type=='VIEW_3D' and c.space_data.local_view else 0)
    cached=_selected_cache.get(key)
    if cached and cached[0]==signature:return cached[1]
    dependencies=Dependencies(c)
    for obj in objects:
        dependencies.object(obj,visible=True)
        if c.space_data and c.space_data.type=='VIEW_3D':
            if not obj.visible_get(view_layer=c.view_layer,viewport=c.space_data):continue
        col=obj.instance_collection
        transform=obj.evaluated_get(graph).matrix_world @ Matrix.Translation(-col.instance_offset)
        points=[transform@p for p in definition_bounds(col,graph,())]
        dependencies.merge(_definition_dependencies[(col.as_pointer(),graph.as_pointer())])
        if len(points)==8:
            if style=='DASHED':
                edges=project_points(points,projection,c.region.width,c.region.height)
                vertices.extend(wire_segments(edges))
            else:vertices.extend(points[i] for edge in EDGES for i in edge)
    _selected_cache[key]=(signature,vertices,dependencies)
    return vertices


def wire_batch(c,kind,values,shader):
    from gpu_extras.batch import batch_for_shader
    key=(c.window.as_pointer(),c.region.as_pointer(),kind)
    entry=_wire_batches.get(key)
    if entry is None or entry[0] is not values:
        if key not in _wire_batches and len(_wire_batches)>=64:_wire_batches.clear()
        vertices=(wire_segments(values) if kind=='EDIT' else
                  [point for edge in values for point in edge[:2]] if kind=='EDIT_SOLID' else values)
        entry=(values,batch_for_shader(shader,'LINES',{'pos':vertices}))
        _wire_batches[key]=entry
    return entry[1]


def draw_wire():
    c=bpy.context
    addon=c.preferences.addons.get(__package__)
    prefs=addon.preferences if addon else None
    style=prefs.edit_bounds_style if prefs else 'DASHED'
    selection=selected_vertices(c)
    edges=projected_box(c) if active(c) and style!='NONE' else []
    if not selection and not edges:return
    import gpu
    global _wire_shader
    if _wire_shader is None:_wire_shader=gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
    shader=_wire_shader
    blend=gpu.state.blend_get();depth=gpu.state.depth_test_get()
    try:
        gpu.state.blend_set('ALPHA');gpu.state.depth_test_set('NONE')
        shader.bind();shader.uniform_float('viewportSize',gpu.state.viewport_get()[2:])
        if selection:
            shader.uniform_float('lineWidth',prefs.selected_bounds_width if prefs else 1.0)
            shader.uniform_float('color',prefs.selected_bounds_color if prefs else (1,.627,.157,.48))
            wire_batch(c,'SELECTED',selection,shader).draw(shader)
        if not edges:return
        shader.uniform_float('lineWidth',prefs.edit_bounds_width if prefs else 1.0)
        shader.uniform_float('color',prefs.edit_bounds_color if prefs else (.66,.69,.72,.52))
        wire_batch(c,'EDIT_SOLID' if style=='SOLID' else 'EDIT',edges,shader).draw(shader)
    finally:
        gpu.state.depth_test_set(depth);gpu.state.blend_set(blend)


def draw():
    c=bpy.context
    if not active(c):return
    import blf
    from .instances import session_chain
    blf.size(0,11*c.preferences.system.ui_scale);blf.color(0,.7,.72,.75,.75)
    blf.position(0,18,18,0)
    work=active(c);target=work.get('sg_session_target')
    blf.draw(0,f'{target.name if target else work.name} · 第 {len(session_chain(work))} 层')



def register():
    global _handle,_wire_handle
    for handlers in (bpy.app.handlers.frame_change_post,bpy.app.handlers.undo_post,bpy.app.handlers.redo_post,bpy.app.handlers.load_post):handlers.append(invalidate)
    bpy.app.handlers.depsgraph_update_post.append(changed)
    if not bpy.app.background:
        _handle=bpy.types.SpaceView3D.draw_handler_add(draw,(),'WINDOW','POST_PIXEL')
        _wire_handle=bpy.types.SpaceView3D.draw_handler_add(draw_wire,(),'WINDOW','POST_VIEW')

def unregister():
    global _handle,_wire_handle,_wire_shader
    if _handle is not None:bpy.types.SpaceView3D.draw_handler_remove(_handle,'WINDOW');_handle=None
    if _wire_handle is not None:bpy.types.SpaceView3D.draw_handler_remove(_wire_handle,'WINDOW');_wire_handle=None
    invalidate();_wire_shader=None
    if changed in bpy.app.handlers.depsgraph_update_post:bpy.app.handlers.depsgraph_update_post.remove(changed)
    for handlers in (bpy.app.handlers.frame_change_post,bpy.app.handlers.undo_post,bpy.app.handlers.redo_post,bpy.app.handlers.load_post):
        if invalidate in handlers:handlers.remove(invalidate)
