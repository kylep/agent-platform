from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

if TYPE_CHECKING:
    from ..models.typed_definition import TypedDefinition


T = TypeVar("T", bound="CreateView")


@_attrs_define
class CreateView:
    """
    Attributes:
        app_name (str):
        definition (TypedDefinition):
        slug (str):
    """

    app_name: str
    definition: TypedDefinition
    slug: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        app_name = self.app_name

        definition = self.definition.to_dict()

        slug = self.slug

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "app_name": app_name,
                "definition": definition,
                "slug": slug,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.typed_definition import TypedDefinition

        d = dict(src_dict)
        app_name = d.pop("app_name")

        definition = TypedDefinition.from_dict(d.pop("definition"))

        slug = d.pop("slug")

        create_view = cls(
            app_name=app_name,
            definition=definition,
            slug=slug,
        )

        create_view.additional_properties = d
        return create_view

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
