from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

if TYPE_CHECKING:
    from ..models.intent_in_arguments import IntentInArguments


T = TypeVar("T", bound="IntentIn")


@_attrs_define
class IntentIn:
    """
    Attributes:
        alias (str):
        arguments (IntentInArguments):
    """

    alias: str
    arguments: IntentInArguments
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        alias = self.alias

        arguments = self.arguments.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "alias": alias,
                "arguments": arguments,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.intent_in_arguments import IntentInArguments

        d = dict(src_dict)
        alias = d.pop("alias")

        arguments = IntentInArguments.from_dict(d.pop("arguments"))

        intent_in = cls(
            alias=alias,
            arguments=arguments,
        )

        intent_in.additional_properties = d
        return intent_in

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
