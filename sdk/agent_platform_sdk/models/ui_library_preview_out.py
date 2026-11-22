from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.ui_library_conflict import UiLibraryConflict
    from ..models.ui_library_preview_out_counts import UiLibraryPreviewOutCounts


T = TypeVar("T", bound="UiLibraryPreviewOut")


@_attrs_define
class UiLibraryPreviewOut:
    """
    Attributes:
        added (list[str]):
        conflicts (list[UiLibraryConflict]):
        counts (UiLibraryPreviewOutCounts):
        library_id (str):
        removed (list[str]):
        slug (str):
        source_version (int):
        updated (list[str]):
        version (int):
        truncated (bool | Unset):  Default: False.
    """

    added: list[str]
    conflicts: list[UiLibraryConflict]
    counts: UiLibraryPreviewOutCounts
    library_id: str
    removed: list[str]
    slug: str
    source_version: int
    updated: list[str]
    version: int
    truncated: bool | Unset = False
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        added = self.added

        conflicts = []
        for conflicts_item_data in self.conflicts:
            conflicts_item = conflicts_item_data.to_dict()
            conflicts.append(conflicts_item)

        counts = self.counts.to_dict()

        library_id = self.library_id

        removed = self.removed

        slug = self.slug

        source_version = self.source_version

        updated = self.updated

        version = self.version

        truncated = self.truncated

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "added": added,
                "conflicts": conflicts,
                "counts": counts,
                "library_id": library_id,
                "removed": removed,
                "slug": slug,
                "source_version": source_version,
                "updated": updated,
                "version": version,
            }
        )
        if truncated is not UNSET:
            field_dict["truncated"] = truncated

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.ui_library_conflict import UiLibraryConflict
        from ..models.ui_library_preview_out_counts import UiLibraryPreviewOutCounts

        d = dict(src_dict)
        added = cast(list[str], d.pop("added"))

        conflicts = []
        _conflicts = d.pop("conflicts")
        for conflicts_item_data in _conflicts:
            conflicts_item = UiLibraryConflict.from_dict(conflicts_item_data)

            conflicts.append(conflicts_item)

        counts = UiLibraryPreviewOutCounts.from_dict(d.pop("counts"))

        library_id = d.pop("library_id")

        removed = cast(list[str], d.pop("removed"))

        slug = d.pop("slug")

        source_version = d.pop("source_version")

        updated = cast(list[str], d.pop("updated"))

        version = d.pop("version")

        truncated = d.pop("truncated", UNSET)

        ui_library_preview_out = cls(
            added=added,
            conflicts=conflicts,
            counts=counts,
            library_id=library_id,
            removed=removed,
            slug=slug,
            source_version=source_version,
            updated=updated,
            version=version,
            truncated=truncated,
        )

        ui_library_preview_out.additional_properties = d
        return ui_library_preview_out

    @property
    def additional_keys(self) -> list[str]:
        return list(self.additional_properties.keys())

    def __getitem__(self, key: str) -> Any:
        return self.additional_properties[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.additional_properties[key] = value

    def __delitem__(self, key: str) -> None:
        del self.additional_properties[key]

    def __contains__(self, key: str) -> bool:
        return key in self.additional_properties
