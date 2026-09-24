"""Scene tools: primitives, transforms, hierarchy, collections, batch, shading."""
# mypy: ignore-errors

import json
import math

import bmesh
import bpy

from . import ToolError, _vec3_schema, register

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


def _asset_context():
    """(collection_name, spec or None) of the active asset, tolerating no bpy extras."""
    try:
        from ..pipeline import facts
        return facts.active_spec(bpy.context.scene)
    except (ImportError, AttributeError):
        return "", None


def _target_collection(collection):
    if collection:
        coll = bpy.data.collections.get(str(collection))
        if coll is None:
            raise ToolError("collection %r not found; call get_scene_state for exact names"
                            % collection)
        return coll
    coll_name, _spec = _asset_context()
    if coll_name and coll_name in bpy.data.collections:
        return bpy.data.collections[coll_name]
    return bpy.context.scene.collection


def _primitive_kwargs(kind, dims, vertices, segments, subdivisions, ring_count,
                      major_segments, minor_segments):
    """Per-kind operator kwargs; sizes come from dimensions afterwards."""
    kw = {}
    r = (dims[0] / 2.0) if dims else None
    if kind == "circle":
        if vertices is not None:
            kw["vertices"] = int(vertices)
        if r is not None:
            kw["radius"] = r
    elif kind == "uv_sphere":
        if segments is not None:
            kw["segments"] = int(segments)
        if ring_count is not None:
            kw["ring_count"] = int(ring_count)
        if r is not None:
            kw["radius"] = r
    elif kind == "ico_sphere":
        if subdivisions is not None:
            kw["subdivisions"] = int(subdivisions)
        if r is not None:
            kw["radius"] = r
    elif kind in ("cylinder", "cone"):
        if vertices is not None:
            kw["vertices"] = int(vertices)
        if r is not None:
            kw["radius"] = r
        if dims:
            kw["depth"] = dims[2]
    elif kind == "torus":
        if major_segments is not None:
            kw["major_segments"] = int(major_segments)
        if minor_segments is not None:
            kw["minor_segments"] = int(minor_segments)
        if r is not None:
            kw["major_radius"] = r
        if dims and dims[2]:
            kw["minor_radius"] = dims[2] / 2.0
    return kw


def create_primitive(  # noqa: PLR0915
        kind, name=None, location=None, rotation=None, scale=None,
        dimensions=None, description=None, role="body", color=None,
        parent=None, collection=None, origin="center",
        segments=None, vertices=None, subdivisions=None,
        ring_count=None, major_segments=None, minor_segments=None):
    """Create a primitive with real-world dimensions, metadata and a color.

    dimensions is [x, y, z] in meters (preferred over scale). origin="bottom"
    places the base at the given location. description/role/color are stored
    as object metadata; a missing color gets a deterministic palette color.
    """
    from ..pipeline import meta
    from ..pipeline.color import apply_object_color
    from ..pipeline.palette import resolve_color
    from ..pipeline.spec import ColorMode

    kind = str(kind).strip().lower().replace(" ", "_").replace("-", "_")
    if kind not in _PRIMITIVE_OPS and kind != QUAD_SPHERE:
        raise ToolError(
            "unknown kind %r; use one of: %s, %s"
            % (kind, ", ".join(sorted(_PRIMITIVE_OPS)), QUAD_SPHERE)
        )
    _ensure_object_mode()
    dims = tuple(float(d) for d in dimensions) if dimensions else None
    if dims is not None and (len(dims) != 3 or any(d <= 0 for d in dims)):
        raise ToolError("dimensions must be 3 positive numbers [x, y, z] in meters")
    kwargs = _primitive_kwargs(kind, dims, vertices, segments, subdivisions,
                               ring_count, major_segments, minor_segments)
    if location is not None:
        kwargs["location"] = location
    if rotation is not None:
        kwargs["rotation"] = [math.radians(a) for a in rotation]
    if scale is not None:
        kwargs["scale"] = scale
    if kind == QUAD_SPHERE:
        obj = _make_quad_sphere(name or "QuadSphere")
        _link_and_select(obj)
        # quad_sphere is built via bmesh, not an operator, so its
        # transform must be applied explicitly.
        _apply_transform(obj, location, rotation, scale)
    else:
        getattr(bpy.ops.mesh, _PRIMITIVE_OPS[kind])(**kwargs)
        obj = bpy.context.active_object
    if dims is not None:
        obj.dimensions = dims
        bpy.context.view_layer.update()
    if origin == "bottom":
        # TRUE bottom origin: shift the MESH so the object origin sits at the
        # base (game semantics: origin at bottom center, base on the floor).
        half = (dims[2] / 2.0) if dims is not None else obj.dimensions.z / 2.0
        for v in obj.data.vertices:
            v.co.z += half
        obj.data.update()
        base_z = location[2] if location is not None else obj.location.z
        obj.location.z = base_z
        bpy.context.view_layer.update()
    if name:
        obj.name = str(name)
    coll = _target_collection(collection)
    for c in list(obj.users_collection):
        c.objects.unlink(obj)
    coll.objects.link(obj)
    if parent:
        obj.parent = _get_object(str(parent))
    coll_name, spec = _asset_context()
    mode = spec.color_mode if spec else ColorMode.VERTEX_COLOR
    meta.ensure_meta(obj, coll_name or "Asset",
                     str(description or ("%s %s" % (kind, obj.name))),
                     str(role))
    if color is not None:
        rgba = resolve_color(color, fallback_name=obj.name)
    else:
        rgba = resolve_color(None, fallback_name=obj.name)
    apply_object_color(obj, rgba, mode, asset=coll_name)
    mesh, _owned = (obj.data, False) if obj.type == "MESH" else (None, False)
    if mesh is not None:
        mesh.calc_loop_triangles()
        tris = len(mesh.loop_triangles)
    else:
        tris = 0
    hint = ("budget tight: reduce segments or add DECIMATE" if spec is not None
            and tris > spec.budget[1]
            else "add BEVEL for rims or continue blockout")
    return json.dumps({"ok": True, "created": obj.name, "tris": tris,
                       "dims": [round(d, 4) for d in obj.dimensions],
                       "hint": hint}, separators=(",", ":"))


def transform_object(object, location=None, rotation=None, scale=None,
                     dimensions=None):
    """Move/rotate/scale an object, or set exact real-world dimensions (m).

    rotation is XYZ in degrees. Omitted axes stay unchanged (each parameter
    replaces the whole transform component). dimensions is preferred over
    scale: it IS the size in meters.
    """
    obj = _get_object(object)
    _apply_transform(obj, location, rotation, scale)
    if dimensions is not None:
        dims = [float(d) for d in dimensions]
        if len(dims) != 3 or any(d <= 0 for d in dims):
            raise ToolError("dimensions must be 3 positive numbers [x, y, z] in meters")
        obj.dimensions = dims
        bpy.context.view_layer.update()
    return "transformed %r (dims %s)" % (
        obj.name, [round(d, 3) for d in obj.dimensions])


def rename_object(old, new):
    obj = _get_object(old)
    obj.name = new
    return "renamed %r -> %r" % (old, obj.name)


def duplicate_object(object, offset=None, name=None, description=None):
    """Duplicate an object; the copy inherits description with a suffix."""
    from ..pipeline import meta

    obj = _get_object(object)
    copy = obj.copy()
    if copy.data:
        copy.data = copy.data.copy()
    if offset is not None:
        copy.location = obj.location + _vec(offset)
    _link_and_select(copy)
    copy.name = str(name) if name else obj.name + "_copy"
    src_desc = meta.get_description(obj)
    meta.set_description(copy, str(description or (src_desc + " (copy)" if src_desc else "copy of %s" % obj.name)))
    role = meta.get_role(obj)
    if role:
        copy["ai_role"] = role
    copy.color = tuple(obj.color)
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
    """Join objects into the first one; merged description keeps every part."""
    from ..pipeline import meta

    if len(objects) < 2:
        raise ToolError("join needs at least 2 objects")
    _ensure_object_mode()
    objs = [_get_object(n) for n in objects]
    target = objs[0]
    merged = meta.merge_descriptions(objs)
    bpy.ops.object.select_all(action='DESELECT')
    for obj in objs:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = target
    bpy.ops.object.join()
    if merged:
        meta.set_description(target, merged)
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


def set_shading(objects, mode="flat", angle=30.0):
    """Set flat, smooth, or smooth-by-angle shading.

    smooth_by_angle marks sharp edges destructively (no modifier), so the
    export stays clean; angle is in degrees.
    """
    modes = ("flat", "smooth", "smooth_by_angle")
    if mode not in modes:
        raise ToolError("mode must be one of %s" % ", ".join(modes))
    parts = []
    for name in objects:
        obj = _get_object(name)
        if obj.type != 'MESH':
            raise ToolError("%r is not a mesh" % name)
        _ensure_object_mode()
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.select_all(action='DESELECT')
        obj.select_set(True)
        if mode == "smooth":
            bpy.ops.object.shade_smooth()
        elif mode == "flat":
            bpy.ops.object.shade_flat()
        else:
            bpy.ops.object.shade_smooth()
            bpy.ops.object.shade_smooth_by_angle(angle=math.radians(float(angle)))
        parts.append(obj.name)
    return "set %s shading on: %s" % (mode, ", ".join(parts))


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
        "Create a mesh primitive with real-world dimensions, description and color: "
        "the ONE blockout tool. kind is one of: plane, cube, circle, uv_sphere, "
        "ico_sphere, cylinder, cone, torus, grid, monkey, quad_sphere (all-quads, "
        "good for sculpting).\n"
        "Use when: stage 1 blockout and simple detail volumes. Do not use when: "
        "editing existing geometry (mesh_op) or duplicating (duplicate_object).\n"
        "Args notes: dimensions [x,y,z] METERS (preferred over scale); origin "
        "bottom places the base at location (props sit on the floor); vertices/"
        "segments/subdivisions cap resolution (cylinder 6-12 for low-poly); "
        "description is REQUIRED (one sentence: what it is, material, function); "
        "role body|detail|trim|foliage|trunk|wheel|handle; color is a palette key "
        "('iron', 'wood_light', ...) or RGBA 0..1 — omitted colors auto-assign; "
        "collection defaults to the active asset collection.\n"
        "Returns: {ok, created, tris, dims, hint}.\n"
        "Example: {\"kind\":\"cylinder\",\"name\":\"SM_Barrel_Body\",\"vertices\":12,"
        "\"dimensions\":[0.6,0.6,0.9],\"origin\":\"bottom\",\"description\":\"Oak barrel "
        "body\",\"role\":\"body\",\"color\":\"wood_light\"}",
        {
            "type": "object",
            "properties": {
                "kind": {"type": "string",
                         "enum": [*sorted(_PRIMITIVE_OPS), QUAD_SPHERE]},
                "name": {"type": "string", "description": "SM_<Asset>_<Part>"},
                "dimensions": {"type": "array", "items": {"type": "number"},
                               "minItems": 3, "maxItems": 3,
                               "description": "Size in METERS [x,y,z]"},
                "location": vec3("World location XYZ (base point if origin=bottom)"),
                "rotation": vec3("Rotation XYZ in degrees"),
                "scale": vec3("Scale XYZ (prefer dimensions)"),
                "origin": {"type": "string", "enum": ["center", "bottom"]},
                "description": {"type": "string",
                                "description": "REQUIRED: what it is, material, function"},
                "role": {"type": "string",
                         "enum": ["body", "detail", "trim", "foliage", "trunk",
                                  "wheel", "handle"]},
                "color": {"type": ["string", "array"],
                          "description": "Palette key or RGBA 0..1; auto if omitted"},
                "parent": {"type": "string", "description": "Parent object name"},
                "collection": {"type": "string", "description": "Target collection (default: active asset)"},
                "vertices": {"type": "integer", "description": "circle/cylinder/cone segment count"},
                "segments": {"type": "integer", "description": "uv_sphere segments"},
                "ring_count": {"type": "integer", "description": "uv_sphere rings"},
                "subdivisions": {"type": "integer", "description": "ico_sphere subdivision level"},
                "major_segments": {"type": "integer", "description": "torus main ring"},
                "minor_segments": {"type": "integer", "description": "torus tube"},
            },
            "required": ["kind", "description"],
        },
        create_primitive,
    )
    register(
        "transform_object",
        "Set absolute location/rotation/scale of an object, or its exact "
        "real-world dimensions in meters. rotation is XYZ Euler in DEGREES. "
        "Omitted axes stay unchanged (each parameter replaces the whole "
        "transform component). dimensions IS the size in meters and is "
        "preferred over scale for blockouts.\n"
        "Returns: confirmation with the resulting dimensions.",
        {
            "type": "object",
            "properties": {
                "object": {"type": "string", "description": "Object name"},
                "location": vec3("New world location XYZ"),
                "rotation": vec3("New rotation XYZ in degrees"),
                "scale": vec3("New scale XYZ"),
                "dimensions": {"type": "array", "items": {"type": "number"},
                               "minItems": 3, "maxItems": 3,
                               "description": "Exact size in METERS [x,y,z]"},
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
        "Duplicate an object with offset; the copy inherits description, role and "
        "color (description gets a suffix unless overridden).\n"
        "Use when: repeated parts (barrel hoops, chair legs, tree crowns).\n"
        "Args: offset [x,y,z] meters; name for the copy (SM_<Asset>_<Part>).\n"
        "Returns: confirmation with both names.",
        {
            "type": "object",
            "properties": {
                "object": {"type": "string"},
                "offset": vec3("Offset added to the copy location"),
                "name": {"type": "string", "description": "Name for the copy"},
                "description": {"type": "string", "description": "Override description"},
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
        "Join several mesh objects into the first one; merged descriptions "
        "survive in the result's ai_description.\n"
        "Use when: same-material volumes become one mesh (hard-surface "
        "workflow). Do not use when: parts must stay separate (wheels, modular "
        "pieces, LODs).",
        {
            "type": "object",
            "properties": {
                "objects": {"type": "array", "items": {"type": "string"},
                            "minItems": 2,
                            "description": "First object absorbs the rest"},
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
        "Set flat, smooth, or smooth-by-angle shading on mesh objects. "
        "Low-poly art wants flat; hard-surface wants smooth_by_angle "
        "(angle in degrees, default 30) — it marks sharp edges WITHOUT a "
        "modifier, so exports stay clean.\n"
        "Returns: confirmation with the affected objects.",
        {
            "type": "object",
            "properties": {
                "objects": {"type": "array", "items": {"type": "string"},
                            "minItems": 1},
                "mode": {"type": "string", "enum": ["flat", "smooth", "smooth_by_angle"]},
                "angle": {"type": "number", "description": "Degrees for smooth_by_angle"},
            },
            "required": ["objects"],
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
                         "enum": [*sorted(_PRIMITIVE_OPS), QUAD_SPHERE]},
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
