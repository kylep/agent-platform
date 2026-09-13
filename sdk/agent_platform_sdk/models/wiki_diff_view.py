from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

if TYPE_CHECKING:
    from ..models.wiki_version_view import WikiVersionView


T = TypeVar("T", bound="WikiDiffView")


@_attrs_define
class WikiDiffView:
    """One version and what it changed, against the version before it.

    Attributes:
        added (int):
        diff (str):
        removed (int):
        version (WikiVersionView):
    """

    added: int
    diff: str
    removed: int
    version: WikiVersionView
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        added = self.added

        diff = self.diff

        removed = self.removed

        version = self.version.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "added": added,
                "diff": diff,
                "removed": removed,
                "version": version,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.wiki_version_view import WikiVersionView

        d = dict(src_dict)
        added = d.pop("added")

        diff = d.pop("diff")

        removed = d.pop("removed")

        version = WikiVersionView.from_dict(d.pop("version"))

        wiki_diff_view = cls(
            added=added,
            diff=diff,
            removed=removed,
            version=version,
        )

        wiki_diff_view.additional_properties = d
        return wiki_diff_view

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
