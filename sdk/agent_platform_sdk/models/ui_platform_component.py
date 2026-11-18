from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.ui_platform_component_property import UiPlatformComponentProperty
    from ..models.ui_platform_component_variant import UiPlatformComponentVariant


T = TypeVar("T", bound="UiPlatformComponent")


@_attrs_define
class UiPlatformComponent:
    """
    Attributes:
        description (str):
        import_path (str):
        limitations (list[str]):
        name (str):
        properties (list[UiPlatformComponentProperty]):
        variants (list[UiPlatformComponentVariant]):
        category (None | str | Unset):
        story_id (None | str | Unset):
    """

    description: str
    import_path: str
    limitations: list[str]
    name: str
    properties: list[UiPlatformComponentProperty]
    variants: list[UiPlatformComponentVariant]
    category: None | str | Unset = UNSET
    story_id: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        description = self.description

        import_path = self.import_path

        limitations = self.limitations

        name = self.name

        properties = []
        for properties_item_data in self.properties:
            properties_item = properties_item_data.to_dict()
            properties.append(properties_item)

        variants = []
        for variants_item_data in self.variants:
            variants_item = variants_item_data.to_dict()
            variants.append(variants_item)

        category: None | str | Unset
        if isinstance(self.category, Unset):
            category = UNSET
        else:
            category = self.category

        story_id: None | str | Unset
        if isinstance(self.story_id, Unset):
            story_id = UNSET
        else:
            story_id = self.story_id

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "description": description,
                "import_path": import_path,
                "limitations": limitations,
                "name": name,
                "properties": properties,
                "variants": variants,
            }
        )
        if category is not UNSET:
            field_dict["category"] = category
        if story_id is not UNSET:
            field_dict["story_id"] = story_id

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.ui_platform_component_property import UiPlatformComponentProperty
        from ..models.ui_platform_component_variant import UiPlatformComponentVariant

        d = dict(src_dict)
        description = d.pop("description")

        import_path = d.pop("import_path")

        limitations = cast(list[str], d.pop("limitations"))

        name = d.pop("name")

        properties = []
        _properties = d.pop("properties")
        for properties_item_data in _properties:
            properties_item = UiPlatformComponentProperty.from_dict(
                properties_item_data
            )

            properties.append(properties_item)

        variants = []
        _variants = d.pop("variants")
        for variants_item_data in _variants:
            variants_item = UiPlatformComponentVariant.from_dict(variants_item_data)

            variants.append(variants_item)

        def _parse_category(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        category = _parse_category(d.pop("category", UNSET))

        def _parse_story_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        story_id = _parse_story_id(d.pop("story_id", UNSET))

        ui_platform_component = cls(
            description=description,
            import_path=import_path,
            limitations=limitations,
            name=name,
            properties=properties,
            variants=variants,
            category=category,
            story_id=story_id,
        )

        ui_platform_component.additional_properties = d
        return ui_platform_component

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
