from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.ui_version_row import UiVersionRow


T = TypeVar("T", bound="UiVersionList")


@_attrs_define
class UiVersionList:
    """
    Attributes:
        slug (str):
        version (int):
        versions (list[UiVersionRow]):
        next_before (int | None | Unset):
    """

    slug: str
    version: int
    versions: list[UiVersionRow]
    next_before: int | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        slug = self.slug

        version = self.version

        versions = []
        for versions_item_data in self.versions:
            versions_item = versions_item_data.to_dict()
            versions.append(versions_item)

        next_before: int | None | Unset
        if isinstance(self.next_before, Unset):
            next_before = UNSET
        else:
            next_before = self.next_before

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "slug": slug,
                "version": version,
                "versions": versions,
            }
        )
        if next_before is not UNSET:
            field_dict["next_before"] = next_before

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.ui_version_row import UiVersionRow

        d = dict(src_dict)
        slug = d.pop("slug")

        version = d.pop("version")

        versions = []
        _versions = d.pop("versions")
        for versions_item_data in _versions:
            versions_item = UiVersionRow.from_dict(versions_item_data)

            versions.append(versions_item)

        def _parse_next_before(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        next_before = _parse_next_before(d.pop("next_before", UNSET))

        ui_version_list = cls(
            slug=slug,
            version=version,
            versions=versions,
            next_before=next_before,
        )

        ui_version_list.additional_properties = d
        return ui_version_list

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
