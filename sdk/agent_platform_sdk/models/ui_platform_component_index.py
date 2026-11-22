from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="UiPlatformComponentIndex")


@_attrs_define
class UiPlatformComponentIndex:
    """
    Attributes:
        description (str):
        import_path (str):
        name (str):
        properties (list[str]):
        variants (list[str]):
        category (None | str | Unset):
        default_height (float | None | str | Unset):
        default_width (float | None | str | Unset):
    """

    description: str
    import_path: str
    name: str
    properties: list[str]
    variants: list[str]
    category: None | str | Unset = UNSET
    default_height: float | None | str | Unset = UNSET
    default_width: float | None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        description = self.description

        import_path = self.import_path

        name = self.name

        properties = self.properties

        variants = self.variants

        category: None | str | Unset
        if isinstance(self.category, Unset):
            category = UNSET
        else:
            category = self.category

        default_height: float | None | str | Unset
        if isinstance(self.default_height, Unset):
            default_height = UNSET
        else:
            default_height = self.default_height

        default_width: float | None | str | Unset
        if isinstance(self.default_width, Unset):
            default_width = UNSET
        else:
            default_width = self.default_width

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "description": description,
                "import_path": import_path,
                "name": name,
                "properties": properties,
                "variants": variants,
            }
        )
        if category is not UNSET:
            field_dict["category"] = category
        if default_height is not UNSET:
            field_dict["default_height"] = default_height
        if default_width is not UNSET:
            field_dict["default_width"] = default_width

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        description = d.pop("description")

        import_path = d.pop("import_path")

        name = d.pop("name")

        properties = cast(list[str], d.pop("properties"))

        variants = cast(list[str], d.pop("variants"))

        def _parse_category(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        category = _parse_category(d.pop("category", UNSET))

        def _parse_default_height(data: object) -> float | None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | str | Unset, data)

        default_height = _parse_default_height(d.pop("default_height", UNSET))

        def _parse_default_width(data: object) -> float | None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | str | Unset, data)

        default_width = _parse_default_width(d.pop("default_width", UNSET))

        ui_platform_component_index = cls(
            description=description,
            import_path=import_path,
            name=name,
            properties=properties,
            variants=variants,
            category=category,
            default_height=default_height,
            default_width=default_width,
        )

        ui_platform_component_index.additional_properties = d
        return ui_platform_component_index

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
