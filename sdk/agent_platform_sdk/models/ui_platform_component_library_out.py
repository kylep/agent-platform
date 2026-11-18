from __future__ import annotations

from collections.abc import Mapping
from typing import (
    TYPE_CHECKING,
    Any,
    Literal,
    TypeVar,
    cast,
)

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.ui_platform_component import UiPlatformComponent
    from ..models.ui_platform_component_index import UiPlatformComponentIndex


T = TypeVar("T", bound="UiPlatformComponentLibraryOut")


@_attrs_define
class UiPlatformComponentLibraryOut:
    """
    Attributes:
        components (list[UiPlatformComponent] | list[UiPlatformComponentIndex]):
        id (Literal['agent-platform']):
        revision (int):
    """

    components: list[UiPlatformComponent] | list[UiPlatformComponentIndex]
    id: Literal["agent-platform"]
    revision: int
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        components: list[dict[str, Any]]
        if isinstance(self.components, list):
            components = []
            for components_type_0_item_data in self.components:
                components_type_0_item = components_type_0_item_data.to_dict()
                components.append(components_type_0_item)

        else:
            components = []
            for components_type_1_item_data in self.components:
                components_type_1_item = components_type_1_item_data.to_dict()
                components.append(components_type_1_item)

        id = self.id

        revision = self.revision

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "components": components,
                "id": id,
                "revision": revision,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.ui_platform_component import UiPlatformComponent
        from ..models.ui_platform_component_index import UiPlatformComponentIndex

        d = dict(src_dict)

        def _parse_components(
            data: object,
        ) -> list[UiPlatformComponent] | list[UiPlatformComponentIndex]:
            try:
                if not isinstance(data, list):
                    raise TypeError()
                components_type_0 = []
                _components_type_0 = data
                for components_type_0_item_data in _components_type_0:
                    components_type_0_item = UiPlatformComponent.from_dict(
                        components_type_0_item_data
                    )

                    components_type_0.append(components_type_0_item)

                return components_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            if not isinstance(data, list):
                raise TypeError()
            components_type_1 = []
            _components_type_1 = data
            for components_type_1_item_data in _components_type_1:
                components_type_1_item = UiPlatformComponentIndex.from_dict(
                    components_type_1_item_data
                )

                components_type_1.append(components_type_1_item)

            return components_type_1

        components = _parse_components(d.pop("components"))

        id = cast(Literal["agent-platform"], d.pop("id"))
        if id != "agent-platform":
            raise ValueError(f"id must match const 'agent-platform', got '{id}'")

        revision = d.pop("revision")

        ui_platform_component_library_out = cls(
            components=components,
            id=id,
            revision=revision,
        )

        ui_platform_component_library_out.additional_properties = d
        return ui_platform_component_library_out

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
