from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

if TYPE_CHECKING:
    from ..models.typed_definition import TypedDefinition


T = TypeVar("T", bound="ReplaceDraft")


@_attrs_define
class ReplaceDraft:
    """
    Attributes:
        definition (TypedDefinition):
        expected_revision (int):
    """

    definition: TypedDefinition
    expected_revision: int
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        definition = self.definition.to_dict()

        expected_revision = self.expected_revision

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "definition": definition,
                "expected_revision": expected_revision,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.typed_definition import TypedDefinition

        d = dict(src_dict)
        definition = TypedDefinition.from_dict(d.pop("definition"))

        expected_revision = d.pop("expected_revision")

        replace_draft = cls(
            definition=definition,
            expected_revision=expected_revision,
        )

        replace_draft.additional_properties = d
        return replace_draft

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
