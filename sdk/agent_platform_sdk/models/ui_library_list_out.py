from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.ui_library_summary import UiLibrarySummary


T = TypeVar("T", bound="UiLibraryListOut")


@_attrs_define
class UiLibraryListOut:
    """
    Attributes:
        libraries (list[UiLibrarySummary]):
        slug (str):
        version (int):
    """

    libraries: list[UiLibrarySummary]
    slug: str
    version: int
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        libraries = []
        for libraries_item_data in self.libraries:
            libraries_item = libraries_item_data.to_dict()
            libraries.append(libraries_item)

        slug = self.slug

        version = self.version

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "libraries": libraries,
                "slug": slug,
                "version": version,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.ui_library_summary import UiLibrarySummary

        d = dict(src_dict)
        libraries = []
        _libraries = d.pop("libraries")
        for libraries_item_data in _libraries:
            libraries_item = UiLibrarySummary.from_dict(libraries_item_data)

            libraries.append(libraries_item)

        slug = d.pop("slug")

        version = d.pop("version")

        ui_library_list_out = cls(
            libraries=libraries,
            slug=slug,
            version=version,
        )

        ui_library_list_out.additional_properties = d
        return ui_library_list_out

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
