"""Scene tools: primitives, transforms, hierarchy, collections, batch, shading."""

import math

import bmesh
import bpy

from . import ToolError, register, _vec3_schema

# Operators present in Blender 5.2 Add > Mesh menu (10) + quad sphere built
# with bmesh (cube -> subdivide -> project onto sphere), since the manual
# documents it as a concept, not an operator.
_PRIMITIVE_OPS = {
    "plane": "primitive_plane_add",
    "cube": "primitive_cube_add",
    "circle": "primitive_circle_add",
    "uv_sphere": "primitive_uv_sphere_add",
    "ico_sphere": "primitive_ico_sphere_add",
    "cylinder": "primitive_cylinder_add",
    "cone": "primitive_cone_add",
    "torus": "primitive_torus_add",
    "grid": "primitive_grid_add",
    "monkey": "primitive_monkey_add",
}
QUAD_SPHERE = "quad_sphere"


def _ensure_object_mode():
    """Force Object Mode before object/mesh bpy.ops tools run.

    In Edit Mode a new primitive merges into the object being edited and
    rename_object renames the user's object; select/join ops also
    poll-fail. Mode changes are data-safe, so they are made implicitly.
    """
    mode = bpy.context.mode
    if mode == "OBJECT":
        return
    try:
        bpy.ops.object.mode_set(mode="OBJECT")
    except RuntimeError as exc:
        raise ToolError("cannot leave %s to run the tool: %s" % (mode, exc)) from exc


def _get_object(name):
    obj = bpy.data.objects.get(name)
    if obj is None:
        raise ToolError("no object named %r" % name)
    return obj


def _apply_transform(obj, location, rotation, scale):
    if location is not None:
        obj.location = location
    if rotation is not None:
        obj.rotation_euler = [math.radians(a) for a in rotation]
    if scale is not None:
        obj.scale = scale


def _link_and_select(obj):
    _ensure_object_mode()
    bpy.context.scene.collection.objects.link(obj)
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def _make_quad_sphere(name, radius=1.0, cuts=6):
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=2.0)
    bmesh.ops.subdivide_edges(bm, edges=bm.edges[:], cuts=cuts, use_grid_fill=True)
    for v in bm.verts:
        v.co.normalize()
        v.co *= radius
    bm.to_mesh(mesh)
    bm.free()
    return bpy.data.objects.new(name, mesh)


def create_primitive(kind, name=None, location=None, rotation=None, scale=None):
    """Create one of the mesh primitives; select and activate it."""
    kind = str(kind).strip().lower().replace(" ", "_").replace("-", "_")
    if kind not in _PRIMITIVE_OPS and kind != QUAD_SPHERE:
        raise ToolError(
            "unknown kind %r; use one of: %s, %s"
            % (kind, ", ".join(sorted(_PRIMITIVE_OPS)), QUAD_SPHERE)
        )
    _ensure_object_mode()
    if kind == QUAD_SPHERE:
        obj = _make_quad_sphere(name or "QuadSphere")
        _link_and_select(obj)
        # quad_sphere is built via bmesh, not an operator, so its
        # transform must be applied explicitly.
        _apply_transform(obj, location, rotation, scale)
    else:
        kwargs = {}
        if location is not None:
            kwargs["location"] = location
        if rotation is not None:
            kwargs["rotation"] = [math.radians(a) for a in rotation]
        if scale is not None:
            kwargs["scale"] = scale
        getattr(bpy.ops.mesh, _PRIMITIVE_OPS[kind])(**kwargs)
        obj = bpy.context.active_object
    if name:
        obj.name = name
    return "created object %r (kind=%s)" % (obj.name, kind)


def transform_object(object, location=None, rotation=None, scale=None):
    """Move/rotate/scale an object. ``rotation`` is XYZ in degrees."""
    obj = _get_object(object)
    _apply_transform(obj, location, rotation, scale)
    return "transformed %r" % obj.name


def rename_object(old, new):
    obj = _get_object(old)
    obj.name = new
    return "renamed %r -> %r" % (old, obj.name)


def duplicate_object(object, offset=None):
    obj = _get_object(object)
    copy = obj.copy()
    if copy.data:
        copy.data = copy.data.copy()
    if offset is not None:
        copy.location = obj.location + _vec(offset)
    _link_and_select(copy)
    copy.name = obj.name + "_copy"
    return "duplicated %r -> %r" % (obj.name, copy.name)


def _vec(values):
    from mathutils import Vector
    return Vector(values[:3])


def delete_objects(objects):
    names = []
    for name in objects:
        obj = _get_object(name)
        names.append(obj.name)
        bpy.data.objects.remove(obj, do_unlink=True)
    return "deleted: %s" % ", ".join(names)


def join_objects(objects):
    """Join objects into the first one."""
    if len(objects) < 2:
        raise ToolError("join needs at least 2 objects")
    _ensure_object_mode()
    objs = [_get_object(n) for n in objects]
    target = objs[0]
    bpy.ops.object.select_all(action='DESELECT')
    for obj in objs:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = target
    bpy.ops.object.join()
    return "joined %d objects into %r" % (len(objects), target.name)


def parent_objects(child, parent, keep_transform=True):
    child_obj = _get_object(child)
    parent_obj = _get_object(parent)
    child_obj.parent = parent_obj
    if keep_transform:
        child_obj.matrix_parent_inverse = parent_obj.matrix_world.inverted()
    return "parented %r under %r" % (child_obj.name, parent_obj.name)


def create_collection(name):
    if bpy.data.collections.get(name):
        raise ToolError("collection %r already exists" % name)
    coll = bpy.data.collections.new(name)
    bpy.context.scene.collection.children.link(coll)
    return "created collection %r" % coll.name


def assign_to_collection(objects, collection):
    coll = bpy.data.collections.get(collection)
    if coll is None:
        raise ToolError("no collection named %r" % collection)
    moved = []
    for name in objects:
        obj = _get_object(name)
        for c in list(obj.users_collection):
            c.objects.unlink(obj)
        coll.objects.link(obj)
        moved.append(obj.name)
    return "moved %s to collection %r" % (", ".join(moved), coll.name)


def set_shading(objects, smooth):
    parts = []
    for name in objects:
        obj = _get_object(name)
        if obj.type != 'MESH':
            raise ToolError("%r is not a mesh" % name)
        for poly in obj.data.polygons:
            poly.use_smooth = smooth
        parts.append(obj.name)
    return "set %s shading on: %s" % ("smooth" if smooth else "flat", ", ".join(parts))


def batch_create(kind, count, arrangement="grid", spacing=2.0, name_prefix="Object"):
    """Create ``count`` primitives laid out in a grid, circle or randomly."""
    count = int(count)
    if count < 1:
        raise ToolError("count must be >= 1")
    if count > 200:
        raise ToolError("count too large (max 200)")
    spacing = float(spacing)
    if spacing <= 0:
        raise ToolError("spacing must be > 0")

    positions = []
    if arrangement == "grid":
        cols = math.ceil(math.sqrt(count))
        for i in range(count):
            positions.append((i % cols * spacing, i // cols * spacing, 0.0))
    elif arrangement == "circle":
        radius = count * spacing / (2.0 * math.pi) if count > 1 else 0.0
        for i in range(count):
            angle = 2.0 * math.pi * i / count
            positions.append((math.cos(angle) * radius, math.sin(angle) * radius, 0.0))
    elif arrangement == "random":
        import random
        rng = random.Random(0)  # deterministic
        extent = math.sqrt(count) * spacing
        for _ in range(count):
            positions.append((rng.uniform(-extent, extent), rng.uniform(-extent, extent), 0.0))
    else:
        raise ToolError("unknown arrangement %r (grid, circle, random)" % arrangement)

    created = []
    for i, location in enumerate(positions):
        create_primitive(kind, name="%s_%03d" % (name_prefix, i + 1), location=location)
        created.append(bpy.context.active_object.name)
    return "created %d objects: %s" % (count, ", ".join(created))


def register_tools():
    vec3 = _vec3_schema
    register(
        "create_primitive",
        "Create a mesh primitive. kind is one of: plane, cube, circle, "
        "uv_sphere, ico_sphere, cylinder, cone, torus, grid, monkey, "
        "quad_sphere (all-quads sphere, good for sculpting). "
        "rotation is XYZ Euler in DEGREES. The new object is selected and active.",
        {
            "type": "object",
            "properties": {
                "kind": {"type": "string",
                         "enum": sorted(_PRIMITIVE_OPS) + [QUAD_SPHERE]},
                "name": {"type": "string", "description": "Object name"},
                "location": vec3("World location XYZ"),
                "rotation": vec3("Rotation XYZ in degrees"),
                "scale": vec3("Scale XYZ"),
            },
            "required": ["kind"],
        },
        create_primitive,
    )
    register(
        "transform_object",
        "Set absolute location/rotation/scale of an object. "
        "rotation is XYZ Euler in DEGREES. Omitted axes stay unchanged "
        "(each parameter replaces the whole transform component).",
        {
            "type": "object",
            "properties": {
                "object": {"type": "string", "description": "Object name"},
                "location": vec3("New world location XYZ"),
                "rotation": vec3("New rotation XYZ in degrees"),
                "scale": vec3("New scale XYZ"),
            },
            "required": ["object"],
        },
        transform_object,
    )
    register(
        "rename_object",
        "Rename an object.",
        {
            "type": "object",
            "properties": {
                "old": {"type": "string"},
                "new": {"type": "string"},
            },
            "required": ["old", "new"],
        },
        rename_object,
    )
    register(
        "duplicate_object",
        "Duplicate an object, optionally offsetting the copy by a vector.",
        {
            "type": "object",
            "properties": {
                "object": {"type": "string"},
                "offset": vec3("Offset added to the copy location"),
            },
            "required": ["object"],
        },
        duplicate_object,
    )
    register(
        "delete_objects",
        "Delete objects by name.",
        {
            "type": "object",
            "properties": {
                "objects": {"type": "array", "items": {"type": "string"},
                            "minItems": 1},
            },
            "required": ["objects"],
        },
        delete_objects,
    )
    register(
        "join_objects",
        "Join several mesh objects into the first one.",
        {
            "type": "object",
            "properties": {
                "objects": {"type": "array", "items": {"type": "string"},
                            "minItems": 2},
            },
            "required": ["objects"],
        },
        join_objects,
    )
    register(
        "parent_objects",
        "Parent child to parent. keep_transform keeps the child world pose.",
        {
            "type": "object",
            "properties": {
                "child": {"type": "string"},
                "parent": {"type": "string"},
                "keep_transform": {"type": "boolean", "default": True},
            },
            "required": ["child", "parent"],
        },
        parent_objects,
    )
    register(
        "create_collection",
        "Create a collection linked to the scene.",
        {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
        create_collection,
    )
    register(
        "assign_to_collection",
        "Move objects into a collection (removes them from their current ones).",
        {
            "type": "object",
            "properties": {
                "objects": {"type": "array", "items": {"type": "string"},
                            "minItems": 1},
                "collection": {"type": "string"},
            },
            "required": ["objects", "collection"],
        },
        assign_to_collection,
    )
    register(
        "set_shading",
        "Set smooth or flat shading on mesh objects.",
        {
            "type": "object",
            "properties": {
                "objects": {"type": "array", "items": {"type": "string"},
                            "minItems": 1},
                "smooth": {"type": "boolean"},
            },
            "required": ["objects", "smooth"],
        },
        set_shading,
    )
    register(
        "batch_create",
        "Create many primitives in a grid, circle or random (deterministic) "
        "arrangement. spacing is the distance between neighbours.",
        {
            "type": "object",
            "properties": {
                "kind": {"type": "string",
                         "enum": sorted(_PRIMITIVE_OPS) + [QUAD_SPHERE]},
                "count": {"type": "integer", "minimum": 1, "maximum": 200},
                "arrangement": {"type": "string",
                                "enum": ["grid", "circle", "random"]},
                "spacing": {"type": "number"},
                "name_prefix": {"type": "string"},
            },
            "required": ["kind", "count"],
        },
        batch_create,
    )


register_tools()
