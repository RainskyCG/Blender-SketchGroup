"""Dependencies of cached drawing data; scene-only updates verify visibility."""
import bpy


def updates(graph):
    ids=set();scene_changed=False
    for update in graph.updates:
        item=update.id.original
        if isinstance(item,bpy.types.Scene):scene_changed=True
        elif isinstance(item,bpy.types.Collection) or update.is_updated_geometry or update.is_updated_transform:
            ids.add(item.as_pointer())
    return ids,scene_changed


class Dependencies:
    def __init__(self,context=None):
        self.ids=set();self.objects={};self.collections={}
        self.layer=context.view_layer if context else None
        self.space=context.space_data if context else None

    def object_state(self,obj,visible):
        state=(obj.hide_viewport,obj.display_type,obj.show_in_front,
               obj.data.as_pointer() if obj.data else 0,obj.instance_type,
               obj.instance_collection.as_pointer() if obj.instance_collection else 0)
        if visible:
            viewport=self.space if self.space and self.space.type=='VIEW_3D' else None
            shown=obj.visible_get(view_layer=self.layer,viewport=viewport) if viewport else obj.visible_get(view_layer=self.layer)
            state+=(obj.hide_get(view_layer=self.layer),shown)
        return state

    @staticmethod
    def collection_state(col):
        return (col.hide_viewport,tuple(col.instance_offset),
                tuple(o.as_pointer() for o in col.objects),tuple(c.as_pointer() for c in col.children))

    def object(self,obj,visible=False):
        key=obj.as_pointer();self.ids.add(key)
        if obj.data:self.ids.add(obj.data.as_pointer())
        old=self.objects.get(key)
        if old and old[1]:visible=True
        self.objects[key]=(obj,visible,self.object_state(obj,visible))
        parent=obj.parent
        while parent:
            self.ids.add(parent.as_pointer());parent=parent.parent

    def collection(self,col):
        key=col.as_pointer();self.ids.add(key)
        if key not in self.collections:self.collections[key]=(col,self.collection_state(col))

    def merge(self,other):
        self.ids.update(other.ids);self.collections.update(other.collections)
        for key,value in other.objects.items():
            if key not in self.objects or not self.objects[key][1]:self.objects[key]=value

    def affected(self,ids,scene_changed):
        if self.ids.intersection(ids):return True
        if not scene_changed:return False
        try:
            return (any(self.collection_state(col)!=state for col,state in self.collections.values()) or
                    any(self.object_state(obj,visible)!=state for obj,visible,state in self.objects.values()))
        except (ReferenceError,RuntimeError):return True
