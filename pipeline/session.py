"""AssetSession: the one sanctioned module-level global.

Owns the active asset collection name, the pinned skill, and the
attachment staging area. Reset in unregister().
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .spec import AssetSpec

__all__ = ("AssetSession", "session")


@dataclass
class AssetSession:
    active_asset: str = ""                    # collection name, e.g. "SM_Barrel"
    pinned_skill: str = ""                    # skill name pinned for this build
    _spec_cache: AssetSpec | None = None
    attachments: list[dict[str, str]] = field(default_factory=list)
    last_capture: str = ""                    # path to the last contact sheet

    def reset(self) -> None:
        self.active_asset = ""
        self.pinned_skill = ""
        self._spec_cache = None
        self.attachments.clear()
        self.last_capture = ""


session = AssetSession()
