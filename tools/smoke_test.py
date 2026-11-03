"""Run only in an isolated factory-startup Blender launched by build_release.py."""
import bpy
import importlib.util
import sys
import tempfile
import zipfile
from pathlib import Path

archive=Path(sys.argv[sys.argv.index('--')+1])
with tempfile.TemporaryDirectory(prefix='sketchgroup-smoke-') as temporary:
    root=Path(temporary)
    with zipfile.ZipFile(archive) as package:
        for name in package.namelist():
            assert (root/name).resolve().is_relative_to(root.resolve())
        package.extractall(root)
    spec=importlib.util.spec_from_file_location('sketchgroup',root/'__init__.py',submodule_search_locations=[str(root)])
    m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m)
    m.register()
    c=bpy.context;f=m.instances
    bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
    bpy.ops.mesh.primitive_cube_add(location=(4,2,1));member=c.object
    assert bpy.ops.sketchgroup.instance_create()=={'FINISHED'}
    target=c.object;source=target.instance_collection
    assert bpy.ops.sketchgroup.instance_duplicate(linked=True)=={'FINISHED'}
    peer=c.object;peer.location.x+=10
    f.choose(c,[target]);assert bpy.ops.sketchgroup.instance_enter()=={'FINISHED'}
    assert f.session(c)==source and member in source.objects.values()
    member.location.z+=2
    assert bpy.ops.sketchgroup.instance_finish()=={'FINISHED'}
    assert target.instance_collection==peer.instance_collection==source
    assert bpy.ops.sketchgroup.instance_duplicate(linked=False)=={'FINISHED'}
    assert c.object.instance_collection!=source
    m.unregister()
    assert not f.session(c)
    print('PACKAGE_SMOKE_PASS',m.RUNTIME_VERSION,flush=True)
