"""Build report: what the model sees after every build_model call.

bpy-free on purpose (unit-testable): the tool layer converts objects
into ``Part`` records, this module finds problems and renders text.
The key check is connectivity: parts whose bounding boxes touch form
one group; more than one group means pieces are floating apart — the
"parts laid out in a row instead of one building" failure.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from typing import Final

__all__ = ("Part", "connected_groups", "render_report")

Vec = tuple[float, float, float]

_TOUCH_TOL: Final = 0.05      # meters: parts closer than this "touch"
_MAX_PART_LINES: Final = 60   # keep the observation bounded
_MAX_GROUP_NAMES: Final = 6


@dataclass(slots=True, frozen=True)
class Part:
    """One mesh part in world space."""

    name: str
    tris: int
    lo: Vec
    hi: Vec
    mesh: str = ""  # shared by linked copies (mk.copy / repeat / radial)

    @property
    def size(self) -> Vec:
        return (self.hi[0] - self.lo[0], self.hi[1] - self.lo[1], self.hi[2] - self.lo[2])

    @property
    def center(self) -> Vec:
        return ((self.hi[0] + self.lo[0]) / 2, (self.hi[1] + self.lo[1]) / 2,
                (self.hi[2] + self.lo[2]) / 2)

    def touches(self, other: Part, tol: float = _TOUCH_TOL) -> bool:
        return all(self.lo[i] - tol <= other.hi[i] and other.lo[i] - tol <= self.hi[i]
                   for i in range(3))


def connected_groups(parts: Sequence[Part], tol: float = _TOUCH_TOL) -> list[list[str]]:
    """Union-find over bbox contact; largest group first."""
    parent = list(range(len(parts)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, a in enumerate(parts):
        for j in range(i + 1, len(parts)):
            if a.touches(parts[j], tol):
                parent[find(i)] = find(j)
    groups: dict[int, list[str]] = {}
    for i, part in enumerate(parts):
        groups.setdefault(find(i), []).append(part.name)
    return sorted(groups.values(), key=len, reverse=True)


def _fmt(vec: Vec, sep: str = "x") -> str:
    return sep.join(f"{c:.2f}" for c in vec)


def _part_lines(parts: Sequence[Part]) -> Iterator[str]:
    """One line per part; linked copies of one mesh collapse into 'name xN'."""
    seen: dict[str, int] = {}
    for part in parts:
        if part.mesh:
            seen[part.mesh] = seen.get(part.mesh, 0) + 1
    shown: set[str] = set()
    for part in parts:
        count = seen.get(part.mesh, 1) if part.mesh else 1
        if part.mesh and part.mesh in shown:
            continue
        shown.add(part.mesh)
        label = f"{part.name} x{count} (linked copies)" if count > 1 else part.name
        yield f"  {label} | {part.tris} | {_fmt(part.size)} | ({_fmt(part.center, ', ')})"


def _issues(parts: Sequence[Part], empties: Iterable[str]) -> list[str]:
    issues = [f"'{name}' has no faces (check the cut/union that produced it)"
              for name in empties]
    groups = connected_groups(parts)
    if len(groups) > 1:
        listed = " | ".join(
            ", ".join(g[:_MAX_GROUP_NAMES]) + (" …" if len(g) > _MAX_GROUP_NAMES else "")
            for g in groups[1:6])
        issues.append(
            f"{len(groups)} disconnected groups — these parts do not touch the main "
            f"body: {listed}. Move them so they attach (or confirm they are "
            "meant to be separate).")
    return issues


def render_report(model: str, parts: Sequence[Part], *, error: str = "",
                  output: str = "", notes: Sequence[str] = (),
                  empties: Sequence[str] = ()) -> str:
    """Compact text observation for the LLM."""
    lines: list[str] = []
    if error:
        lines.append(f"BUILD ERROR in model '{model}' — the script stopped here:\n{error}")
        lines.append("Parts built before the error are still in the scene (listed below).")
    if parts:
        lo = tuple(min(p.lo[i] for p in parts) for i in range(3))
        hi = tuple(max(p.hi[i] for p in parts) for i in range(3))
        size = (hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2])
        total = sum(p.tris for p in parts)
        head = "BUILD OK" if not error else "PARTIAL"
        lines.append(f"{head} model='{model}' parts={len(parts)} tris={total} "
                     f"size={_fmt(size)} m  z={lo[2]:.2f}..{hi[2]:.2f}")
        lines.append("parts: name | tris each | size m | center")
        part_lines = list(_part_lines(parts))
        lines.extend(part_lines[:_MAX_PART_LINES])
        if len(part_lines) > _MAX_PART_LINES:
            lines.append(f"  … {len(part_lines) - _MAX_PART_LINES} more parts")
    elif not error:
        lines.append(f"BUILD EMPTY: model '{model}' has no mesh parts — the script "
                     "created nothing.")
    issues = [*notes, *_issues(parts, empties)]
    if issues:
        lines.append("issues:")
        lines.extend(f"  - {text}" for text in issues)
    if output:
        lines.append(f"script output:\n{output}")
    lines.append("next: compare against the request (shape, proportions, every "
                 "requested feature). If anything is missing or wrong, edit the "
                 "script and call build_model again with the FULL script; if it "
                 "matches, call finish.")
    return "\n".join(lines)
