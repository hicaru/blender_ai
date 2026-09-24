"""Skill index: TOML-front-matter Markdown recipes, built-in + user directory.

Progressive disclosure: the index (one line per skill) is always in the
prompt; bodies load on demand via load_skill or automatically when the
request text matches a skill's triggers.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Final

__all__ = ("BUILTIN_DIR", "Skill", "SkillError", "SkillIndex", "parse_skill")

_FRONT: Final = re.compile(r"\A\+\+\+\n(.*?)\n\+\+\+\n(.*)\Z", re.DOTALL)
_WORD: Final = re.compile(r"[a-z0-9]+")
BUILTIN_DIR: Final = Path(__file__).with_name("builtin")


@dataclass(slots=True, frozen=True)
class Skill:
    name: str
    description: str
    triggers: frozenset[str]
    category: str
    body: str
    path: Path
    tri_budget: tuple[int, int] | None = None

    def index_line(self) -> str:
        return f"- {self.name} [{self.category}]: {self.description}"

    def score(self, words: frozenset[str]) -> int:
        return len(self.triggers & words)


class SkillError(ValueError):
    """A skill file is malformed."""


def parse_skill(path: Path) -> Skill:
    text = path.read_text(encoding="utf-8")
    match = _FRONT.match(text)
    if match is None:
        raise SkillError(f"{path.name}: missing +++ TOML front matter")
    try:
        meta = tomllib.loads(match.group(1))
    except tomllib.TOMLDecodeError as err:
        raise SkillError(f"{path.name}: {err}") from err
    budget = meta.get("tri_budget")
    return Skill(
        name=str(meta["name"]),
        description=str(meta["description"]),
        triggers=frozenset(map(str.lower, meta.get("triggers", ()))),
        category=str(meta.get("category", "general")),
        body=match.group(2).strip(),
        path=path,
        tri_budget=(int(budget[0]), int(budget[1])) if budget else None,
    )


def _iter_skills(dirs: Iterable[Path]) -> Iterator[Skill | SkillError]:
    for directory in dirs:
        for path in sorted(directory.glob("*.md")):
            try:
                yield parse_skill(path)
            except (SkillError, KeyError, OSError, UnicodeDecodeError) as err:
                yield SkillError(f"{path.name}: {err}")


@dataclass(slots=True, frozen=True)
class SkillIndex:
    skills: tuple[Skill, ...]
    errors: tuple[str, ...]

    @classmethod
    def load(cls, user_dir: Path | None = None) -> SkillIndex:
        dirs = (BUILTIN_DIR,) if user_dir is None else (BUILTIN_DIR, user_dir)
        by_name: dict[str, Skill] = {}
        errors: list[str] = []
        for item in _iter_skills(dirs):
            match item:
                case Skill():
                    by_name[item.name] = item  # user dir loads last -> overrides built-in
                case SkillError():
                    errors.append(str(item))
        return cls(tuple(by_name.values()), tuple(errors))

    def get(self, name: str) -> Skill | None:
        return next((s for s in self.skills if s.name == name), None)

    def index_block(self) -> str:
        return "\n".join(s.index_line() for s in self.skills)

    def best_match(self, text: str) -> Skill | None:
        words = frozenset(_WORD.findall(text.lower()))
        best = max(self.skills, key=lambda s: s.score(words), default=None)
        return best if best is not None and best.score(words) > 0 else None
