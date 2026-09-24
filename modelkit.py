"""mk — the modeling kit every build script gets as ``mk``.

Why a kit instead of many small tools: an LLM places parts coherently
when ONE script holds every coordinate (parts are positioned relative
to each other in the same place), and re-running the whole script
rebuilds the model deterministically. Dozens of isolated tool calls
lose that spatial frame — the "parts scattered on a line" failure.

Conventions (also stated in the system prompt):

- meters, Z up, ground at z = 0, the model's FRONT faces -Y;
- ``at`` is the part's bottom-center by default (``anchor="bottom"``),
  or its center with ``anchor="center"``;
- every mesh is built directly with bmesh (no bpy.ops, so no context
  errors), lands in the model's collection and gets a material;
- modifiers are baked immediately, so the scene equals the export.

Main thread only (bpy is not thread-safe).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Literal

import bmesh
import bpy
from mathutils import Matrix, Vector

__all__ = ("PALETTE", "Kit", "KitError", "Surface")

Axis = Literal["X", "Y", "Z"]
Anchor = Literal["bottom", "center"]
Vec3 = Sequence[float]

_SMOOTH_ANGLE: Final = math.radians(40.0)
_MIN_SIZE: Final = 1e-4


class KitError(ValueError):
    """Invalid kit call; the message tells the model how to fix it."""


@dataclass(slots=True, frozen=True)
class Surface:
    """A palette entry: sRGB hex color plus PBR values Bevy understands."""

    hex: str
    metallic: float = 0.0
    roughness: float = 0.7
    emission: float = 0.0


PALETTE: Final[Mapping[str, Surface]] = MappingProxyType({
    "concrete": Surface("#8a8a85", 0.0, 0.9),
    "concrete_dark": Surface("#55554f", 0.0, 0.9),
    "steel": Surface("#9aa0a6", 1.0, 0.35),
    "dark_steel": Surface("#3c4043", 1.0, 0.45),
    "rust": Surface("#8b4a2b", 0.3, 0.8),
    "hazard_yellow": Surface("#e0b81f", 0.0, 0.6),
    "military_green": Surface("#4b5a3a", 0.0, 0.7),
    "red": Surface("#b3261e", 0.0, 0.6),
    "blue": Surface("#2f5d9e", 0.0, 0.6),
    "white": Surface("#e8e8e3", 0.0, 0.6),
    "black": Surface("#151515", 0.0, 0.7),
    "rubber": Surface("#202020", 0.0, 0.95),
    "wood": Surface("#8a5a33", 0.0, 0.8),
    "glass": Surface("#a8d8e8", 0.0, 0.05),
    "rock": Surface("#6e6a64", 0.0, 0.95),
    "dirt": Surface("#6b4f35", 0.0, 1.0),
    "grass": Surface("#4f7a35", 0.0, 0.9),
    "sand": Surface("#c9b27c", 0.0, 0.95),
    "water": Surface("#2b5f7a", 0.0, 0.1),
    "light_warm": Surface("#ffd9a0", 0.0, 0.5, 6.0),
    "light_cold": Surface("#cfe8ff", 0.0, 0.5, 6.0),
    "light_red": Surface("#ff3020", 0.0, 0.5, 4.0),
})

_AXIS_ROT: Final[Mapping[str, Matrix]] = MappingProxyType({
    "Z": Matrix.Identity(4),
    "X": Matrix.Rotation(math.radians(90.0), 4, "Y"),
    "Y": Matrix.Rotation(math.radians(90.0), 4, "X"),
})


def _srgb_to_linear(channel: float) -> float:
    if channel <= 0.04045:
        return channel / 12.92
    return float(((channel + 0.055) / 1.055) ** 2.4)


def _hex_rgba(value: str) -> tuple[float, float, float, float]:
    raw = value.lstrip("#")
    if len(raw) != 6:
        raise KitError(f"color {value!r} must be '#rrggbb'")
    try:
        rgb = tuple(int(raw[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    except ValueError as err:
        raise KitError(f"color {value!r} is not hex") from err
    return (*(_srgb_to_linear(c) for c in rgb), 1.0)  # type: ignore[return-value]


def _vec3(value: Vec3, what: str) -> Vector:
    try:
        vec = Vector(tuple(float(c) for c in value))
    except (TypeError, ValueError) as err:
        raise KitError(f"{what} must be 3 numbers, got {value!r}") from err
    if len(vec) != 3:
        raise KitError(f"{what} must be 3 numbers, got {value!r}")
    return vec


def _positive(value: float, what: str) -> float:
    number = float(value)
    if number <= _MIN_SIZE:
        raise KitError(f"{what} must be > 0 (meters), got {value!r}")
    return number


def _set_smooth(bm: bmesh.types.BMesh, angle: float) -> None:
    """Smooth faces, sharp edges above ``angle`` (keeps caps crisp)."""
    for face in bm.faces:
        face.smooth = True
    for edge in bm.edges:
        if len(edge.link_faces) == 2 and edge.calc_face_angle(0.0) > angle:
            edge.smooth = False


class Kit:
    """The ``mk`` object of one build: creates parts inside ``collection``."""

    __slots__ = ("collection", "notes")

    def __init__(self, collection: bpy.types.Collection) -> None:
        self.collection: bpy.types.Collection = collection
        self.notes: list[str] = []

    # ------------------------------------------------------------ materials

    def mat(self, name: str, color: str = "#cccccc", metallic: float = 0.0,
            roughness: float = 0.7, emission: float = 0.0) -> bpy.types.Material:
        """Get or create a Principled material (glTF/Bevy StandardMaterial)."""
        existing = bpy.data.materials.get(name)
        if existing is not None:
            return existing
        material = bpy.data.materials.new(name)
        material.use_nodes = True
        tree = material.node_tree
        bsdf = next((n for n in tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
        if bsdf is None:
            raise KitError(f"material {name!r}: no Principled BSDF node")
        rgba = _hex_rgba(color)
        bsdf.inputs["Base Color"].default_value = rgba
        bsdf.inputs["Metallic"].default_value = float(metallic)
        bsdf.inputs["Roughness"].default_value = float(roughness)
        if emission > 0.0:
            bsdf.inputs["Emission Color"].default_value = rgba
            bsdf.inputs["Emission Strength"].default_value = float(emission)
        material.diffuse_color = rgba  # solid-viewport color matches
        return material

    def _material(self, mat: str | bpy.types.Material | None) -> bpy.types.Material | None:
        match mat:
            case None:
                return None
            case bpy.types.Material():
                return mat
            case str() if mat in PALETTE:
                surface = PALETTE[mat]
                return self.mat(f"M_{mat}", surface.hex, surface.metallic,
                                surface.roughness, surface.emission)
            case str() if mat in bpy.data.materials:
                return bpy.data.materials[mat]
            case _:
                raise KitError(
                    f"unknown material {mat!r}; use a palette key "
                    f"({', '.join(PALETTE)}) or create one with mk.mat(name, '#rrggbb')")

    # ------------------------------------------------------------ core

    def _finish(self, name: str, bm: bmesh.types.BMesh, at: Vec3, anchor: Anchor,
                rot: Vec3, mat: str | bpy.types.Material | None,
                smooth: bool) -> bpy.types.Object:
        """bmesh centered at the origin -> anchored object in the collection."""
        if anchor == "bottom" and bm.verts:
            lift = -min(v.co.z for v in bm.verts)
            bmesh.ops.translate(bm, vec=Vector((0.0, 0.0, lift)), verts=bm.verts[:])
        elif anchor != "center":
            raise KitError(f"anchor must be 'bottom' or 'center', got {anchor!r}")
        if smooth:
            _set_smooth(bm, _SMOOTH_ANGLE)
        mesh = bpy.data.meshes.new(name)
        bm.to_mesh(mesh)
        bm.free()
        obj = bpy.data.objects.new(name, mesh)
        obj.location = _vec3(at, "at")
        obj.rotation_euler = [math.radians(a) for a in _vec3(rot, "rot")]
        material = self._material(mat)
        if material is not None:
            mesh.materials.append(material)
        self.collection.objects.link(obj)
        return obj

    @staticmethod
    def _orient(bm: bmesh.types.BMesh, axis: str) -> None:
        matrix = _AXIS_ROT.get(axis.upper())
        if matrix is None:
            raise KitError(f"axis must be 'X', 'Y' or 'Z', got {axis!r}")
        bmesh.ops.transform(bm, matrix=matrix, verts=bm.verts[:])

    # ------------------------------------------------------------ primitives

    def box(self, name: str, size: Vec3, at: Vec3 = (0, 0, 0), *,
            mat: str | bpy.types.Material | None = None, rot: Vec3 = (0, 0, 0),
            anchor: Anchor = "bottom") -> bpy.types.Object:
        """Box of ``size`` = (x, y, z) meters."""
        dims = _vec3(size, "size")
        for part, what in zip(dims, ("size.x", "size.y", "size.z"), strict=True):
            _positive(part, what)
        bm = bmesh.new()
        bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.scale(bm, vec=dims, verts=bm.verts[:])
        return self._finish(name, bm, at, anchor, rot, mat, smooth=False)

    def cylinder(self, name: str, radius: float, height: float, at: Vec3 = (0, 0, 0), *,
                 verts: int = 24, axis: Axis = "Z", mat: str | bpy.types.Material | None = None,
                 rot: Vec3 = (0, 0, 0), anchor: Anchor = "bottom") -> bpy.types.Object:
        """Solid cylinder; ``axis`` X/Y lays it down (pipes, rollers)."""
        return self.cone(name, radius, radius, height, at, verts=verts, axis=axis,
                         mat=mat, rot=rot, anchor=anchor)

    def cone(self, name: str, radius_bottom: float, radius_top: float, height: float,
             at: Vec3 = (0, 0, 0), *, verts: int = 24, axis: Axis = "Z",
             mat: str | bpy.types.Material | None = None, rot: Vec3 = (0, 0, 0),
             anchor: Anchor = "bottom") -> bpy.types.Object:
        """Truncated cone (radius_top may be 0 for a point)."""
        bm = bmesh.new()
        bmesh.ops.create_cone(
            bm, cap_ends=True, cap_tris=False, segments=max(3, int(verts)),
            radius1=_positive(radius_bottom, "radius_bottom"),
            radius2=max(0.0, float(radius_top)),
            depth=_positive(height, "height"))
        self._orient(bm, axis)
        return self._finish(name, bm, at, anchor, rot, mat, smooth=True)

    def tube(self, name: str, radius: float, thickness: float, height: float,
             at: Vec3 = (0, 0, 0), *, verts: int = 32, axis: Axis = "Z",
             mat: str | bpy.types.Material | None = None, rot: Vec3 = (0, 0, 0),
             anchor: Anchor = "bottom") -> bpy.types.Object:
        """Hollow cylinder (silo shaft, pipe, ring wall). ``radius`` is outer."""
        outer = _positive(radius, "radius")
        inner = outer - _positive(thickness, "thickness")
        if inner <= _MIN_SIZE:
            raise KitError("thickness must be smaller than radius")
        depth = _positive(height, "height")
        count = max(3, int(verts))
        bm = bmesh.new()
        rings: list[list[bmesh.types.BMVert]] = []
        for r, z in ((outer, -depth / 2), (outer, depth / 2),
                     (inner, depth / 2), (inner, -depth / 2)):
            rings.append([
                bm.verts.new((r * math.cos(2 * math.pi * i / count),
                              r * math.sin(2 * math.pi * i / count), z))
                for i in range(count)])
        for ring_a, ring_b in zip(rings, rings[1:] + rings[:1], strict=True):
            for i in range(count):
                j = (i + 1) % count
                bm.faces.new((ring_a[i], ring_a[j], ring_b[j], ring_b[i]))
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
        self._orient(bm, axis)
        return self._finish(name, bm, at, anchor, rot, mat, smooth=True)

    def sphere(self, name: str, radius: float, at: Vec3 = (0, 0, 0), *,
               segments: int = 24, rings: int = 12, hemi: bool = False,
               mat: str | bpy.types.Material | None = None, rot: Vec3 = (0, 0, 0),
               anchor: Anchor = "bottom") -> bpy.types.Object:
        """UV sphere; ``hemi=True`` makes a closed dome (flat side down)."""
        bm = bmesh.new()
        bmesh.ops.create_uvsphere(bm, u_segments=max(3, int(segments)),
                                  v_segments=max(2, int(rings) + int(rings) % 2),
                                  radius=_positive(radius, "radius"))
        if hemi:
            below = [v for v in bm.verts if v.co.z < -1e-5]
            bmesh.ops.delete(bm, geom=below, context="VERTS")
            bmesh.ops.holes_fill(bm, edges=[e for e in bm.edges if e.is_boundary], sides=0)
            bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
        return self._finish(name, bm, at, anchor, rot, mat, smooth=True)

    def torus(self, name: str, radius: float, thickness: float, at: Vec3 = (0, 0, 0), *,
              segments: int = 24, sides: int = 8, axis: Axis = "Z",
              mat: str | bpy.types.Material | None = None, rot: Vec3 = (0, 0, 0),
              anchor: Anchor = "bottom") -> bpy.types.Object:
        """Ring (valve wheel, hoop).

        ``radius`` reaches the tube center; ``thickness`` is the tube diameter.
        """
        major = _positive(radius, "radius")
        minor = _positive(thickness, "thickness") / 2
        seg, side = max(3, int(segments)), max(3, int(sides))
        bm = bmesh.new()
        grid = [[bm.verts.new((
            (major + minor * math.cos(2 * math.pi * s / side)) * math.cos(2 * math.pi * i / seg),
            (major + minor * math.cos(2 * math.pi * s / side)) * math.sin(2 * math.pi * i / seg),
            minor * math.sin(2 * math.pi * s / side)))
            for s in range(side)] for i in range(seg)]
        for i in range(seg):
            for s in range(side):
                a, b = grid[i], grid[(i + 1) % seg]
                bm.faces.new((a[s], b[s], b[(s + 1) % side], a[(s + 1) % side]))
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
        self._orient(bm, axis)
        return self._finish(name, bm, at, anchor, rot, mat, smooth=True)

    def profile(self, name: str, points: Iterable[Sequence[float]], depth: float,
                at: Vec3 = (0, 0, 0), *, plane: Literal["XZ", "XY"] = "XZ",
                mat: str | bpy.types.Material | None = None,
                rot: Vec3 = (0, 0, 0)) -> bpy.types.Object:
        """Extrude a 2D outline: arches, blast-door frames, floor plans, ramps.

        plane="XZ": points are (x, z) as seen from the front, extruded
        ``depth`` along Y (centered). plane="XY": points are (x, y) of a
        footprint, extruded ``depth`` upward from z = 0. Points are exact
        coordinates relative to ``at`` (no anchoring).
        """
        pts = [tuple(float(c) for c in p) for p in points]
        if len(pts) < 3 or any(len(p) != 2 for p in pts):
            raise KitError("profile needs >= 3 points of (u, v)")
        height = _positive(depth, "depth")
        bm = bmesh.new()
        if plane == "XZ":
            base = [bm.verts.new((u, -height / 2, v)) for u, v in pts]
            vec = Vector((0.0, height, 0.0))
        elif plane == "XY":
            base = [bm.verts.new((u, v, 0.0)) for u, v in pts]
            vec = Vector((0.0, 0.0, height))
        else:
            raise KitError(f"plane must be 'XZ' or 'XY', got {plane!r}")
        face = bm.faces.new(base)
        extruded = bmesh.ops.extrude_face_region(bm, geom=[face])
        moved = [g for g in extruded["geom"] if isinstance(g, bmesh.types.BMVert)]
        bmesh.ops.translate(bm, vec=vec, verts=moved)
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
        return self._finish(name, bm, at, "center", rot, mat, smooth=False)

    def stairs(self, name: str, width: float, height: float, length: float, steps: int,
               at: Vec3 = (0, 0, 0), *, mat: str | bpy.types.Material | None = None,
               rot: Vec3 = (0, 0, 0)) -> bpy.types.Object:
        """Solid staircase rising toward +Y from ``at`` (bottom-front-center)."""
        count = max(1, int(steps))
        w = _positive(width, "width")
        rise = _positive(height, "height") / count
        run = _positive(length, "length") / count
        pts = [(0.0, 0.0), (count * run, 0.0)]
        for i in range(count, 0, -1):
            pts += [(i * run, i * rise), ((i - 1) * run, i * rise)]
        bm = bmesh.new()
        ring = [bm.verts.new((-w / 2, y, z)) for y, z in pts]
        face = bm.faces.new(ring)
        extruded = bmesh.ops.extrude_face_region(bm, geom=[face])
        moved = [g for g in extruded["geom"] if isinstance(g, bmesh.types.BMVert)]
        bmesh.ops.translate(bm, vec=Vector((w, 0.0, 0.0)), verts=moved)
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
        return self._finish(name, bm, at, "center", rot, mat, smooth=False)

    # ------------------------------------------------------------ operations

    def _bake(self, obj: bpy.types.Object, modifier: bpy.types.Modifier) -> bpy.types.Object:
        """Apply one modifier without bpy.ops (context-free, headless-safe)."""
        depsgraph = bpy.context.evaluated_depsgraph_get()
        depsgraph.update()
        evaluated = obj.evaluated_get(depsgraph)
        baked = bpy.data.meshes.new_from_object(evaluated, depsgraph=depsgraph)
        old = obj.data
        name = old.name
        obj.modifiers.remove(modifier)
        obj.data = baked
        if old.users == 0:
            bpy.data.meshes.remove(old)
        baked.name = name
        return obj

    def _boolean(self, target: bpy.types.Object, others: Sequence[bpy.types.Object],
                 operation: str) -> bpy.types.Object:
        if not others:
            raise KitError("pass at least one object to combine with")
        for other in others:
            modifier = target.modifiers.new(f"mk_{operation.lower()}", "BOOLEAN")
            modifier.operation = operation
            modifier.solver = "EXACT"
            modifier.object = other
            self._bake(target, modifier)
        for other in others:
            self.delete(other)
        if not target.data.polygons:
            self.notes.append(f"{operation.lower()} left '{target.name}' empty — "
                              "the cutter covers the whole part")
        return target

    def cut(self, target: bpy.types.Object, *cutters: bpy.types.Object) -> bpy.types.Object:
        """Boolean difference (doorways, windows, hatches); cutters are deleted.

        Make cutters slightly deeper than the wall (e.g. wall 0.4 → cutter 0.6)
        so they pass fully through.
        """
        return self._boolean(target, cutters, "DIFFERENCE")

    def union(self, target: bpy.types.Object, *others: bpy.types.Object) -> bpy.types.Object:
        """Boolean union into ``target``; ``others`` are deleted."""
        return self._boolean(target, others, "UNION")

    def join(self, name: str, *objs: bpy.types.Object) -> bpy.types.Object:
        """Merge meshes into one object (fewer draw calls in Bevy); keeps materials."""
        if not objs:
            raise KitError("join needs at least one object")
        first = objs[0]
        if len(objs) > 1:
            with bpy.context.temp_override(active_object=first, object=first,
                                           selected_editable_objects=list(objs)):
                bpy.ops.object.join()
        first.name = name
        first.data.name = name
        return first

    def bevel(self, obj: bpy.types.Object, width: float = 0.03, segments: int = 2,
              angle: float = 30.0) -> bpy.types.Object:
        """Round hard edges sharper than ``angle`` degrees (baked)."""
        modifier = obj.modifiers.new("mk_bevel", "BEVEL")
        modifier.width = _positive(width, "width")
        modifier.segments = max(1, int(segments))
        modifier.limit_method = "ANGLE"
        modifier.angle_limit = math.radians(float(angle))
        return self._bake(obj, modifier)

    def smooth(self, obj: bpy.types.Object, angle: float = 40.0) -> bpy.types.Object:
        """Smooth shading with sharp edges above ``angle`` degrees."""
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        _set_smooth(bm, math.radians(float(angle)))
        bm.to_mesh(obj.data)
        bm.free()
        return obj

    def copy(self, obj: bpy.types.Object, name: str, at: Vec3 | None = None, *,
             rot: Vec3 | None = None, linked: bool = True) -> bpy.types.Object:
        """Duplicate; ``linked`` shares the mesh (one mesh, many instances in Bevy)."""
        dup = obj.copy()
        if not linked:
            dup.data = obj.data.copy()
        dup.name = name
        if at is not None:
            dup.location = _vec3(at, "at")
        if rot is not None:
            dup.rotation_euler = [math.radians(a) for a in _vec3(rot, "rot")]
        self.collection.objects.link(dup)
        return dup

    def repeat(self, obj: bpy.types.Object, count: int, step: Vec3) -> list[bpy.types.Object]:
        """``count`` total copies in a line, ``step`` meters apart (includes ``obj``)."""
        offset = _vec3(step, "step")
        return [obj, *(
            self.copy(obj, f"{obj.name}_{i}", obj.location + offset * i)
            for i in range(1, max(1, int(count))))]

    def radial(self, obj: bpy.types.Object, count: int,
               center: Vec3 = (0, 0, 0)) -> list[bpy.types.Object]:
        """``count`` copies rotated evenly around a vertical axis through ``center``."""
        pivot = _vec3(center, "center")
        total = max(1, int(count))
        bpy.context.view_layer.update()  # matrix_world of fresh objects is stale
        out = [obj]
        for i in range(1, total):
            turn = Matrix.Rotation(2 * math.pi * i / total, 4, "Z")
            dup = self.copy(obj, f"{obj.name}_{i}")
            dup.matrix_world = (Matrix.Translation(pivot) @ turn
                                @ Matrix.Translation(-pivot) @ obj.matrix_world)
            out.append(dup)
        return out

    def mirror(self, obj: bpy.types.Object, axis: Axis = "X",
               name: str | None = None) -> bpy.types.Object:
        """Mirrored copy across the world plane axis = 0 (no negative scale)."""
        index = "XYZ".find(axis.upper())
        if index < 0 or len(axis) != 1:
            raise KitError(f"axis must be 'X', 'Y' or 'Z', got {axis!r}")
        flip = Matrix.Identity(4)
        flip[index][index] = -1.0
        bpy.context.view_layer.update()  # matrix_world of fresh objects is stale
        world = flip @ obj.matrix_world
        origin = world.to_translation()
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        bmesh.ops.transform(bm, matrix=Matrix.Translation(-origin) @ world, verts=bm.verts[:])
        bmesh.ops.reverse_faces(bm, faces=bm.faces[:])
        mesh = bpy.data.meshes.new(name or f"{obj.name}_mirror")
        bm.to_mesh(mesh)
        bm.free()
        for material in obj.data.materials:
            mesh.materials.append(material)
        dup = bpy.data.objects.new(mesh.name, mesh)
        dup.location = origin
        self.collection.objects.link(dup)
        return dup

    def group(self, name: str, *children: bpy.types.Object,
              at: Vec3 = (0, 0, 0)) -> bpy.types.Object:
        """Empty parent (a Bevy entity with children), e.g. a door to animate."""
        empty = bpy.data.objects.new(name, None)
        empty.location = _vec3(at, "at")
        self.collection.objects.link(empty)
        bpy.context.view_layer.update()
        for child in children:
            world = child.matrix_world.copy()
            child.parent = empty
            child.matrix_world = world
        return empty

    def delete(self, obj: bpy.types.Object) -> None:
        """Remove a part (and its mesh when nothing else uses it)."""
        data = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if isinstance(data, bpy.types.Mesh) and data.users == 0:
            bpy.data.meshes.remove(data)
