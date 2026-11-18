from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.ui_platform_component_variant_fixture import (
        UiPlatformComponentVariantFixture,
    )
    from ..models.ui_platform_component_variant_props import (
        UiPlatformComponentVariantProps,
    )
    from ..models.ui_platform_component_variant_selection import (
        UiPlatformComponentVariantSelection,
    )


T = TypeVar("T", bound="UiPlatformComponentVariant")


@_attrs_define
class UiPlatformComponentVariant:
    """
    Attributes:
        fixture (UiPlatformComponentVariantFixture):
        height (float):
        id (str):
        label (str):
        props (UiPlatformComponentVariantProps):
        selection (UiPlatformComponentVariantSelection):
        width (float):
    """

    fixture: UiPlatformComponentVariantFixture
    height: float
    id: str
    label: str
    props: UiPlatformComponentVariantProps
    selection: UiPlatformComponentVariantSelection
    width: float
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        fixture = self.fixture.to_dict()

        height = self.height

        id = self.id

        label = self.label

        props = self.props.to_dict()

        selection = self.selection.to_dict()

        width = self.width

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "fixture": fixture,
                "height": height,
                "id": id,
                "label": label,
                "props": props,
                "selection": selection,
                "width": width,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.ui_platform_component_variant_fixture import (
            UiPlatformComponentVariantFixture,
        )
        from ..models.ui_platform_component_variant_props import (
            UiPlatformComponentVariantProps,
        )
        from ..models.ui_platform_component_variant_selection import (
            UiPlatformComponentVariantSelection,
        )

        d = dict(src_dict)
        fixture = UiPlatformComponentVariantFixture.from_dict(d.pop("fixture"))

        height = d.pop("height")

        id = d.pop("id")

        label = d.pop("label")

        props = UiPlatformComponentVariantProps.from_dict(d.pop("props"))

        selection = UiPlatformComponentVariantSelection.from_dict(d.pop("selection"))

        width = d.pop("width")

        ui_platform_component_variant = cls(
            fixture=fixture,
            height=height,
            id=id,
            label=label,
            props=props,
            selection=selection,
            width=width,
        )

        ui_platform_component_variant.additional_properties = d
        return ui_platform_component_variant

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
