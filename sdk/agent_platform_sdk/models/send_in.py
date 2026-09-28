from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="SendIn")


@_attrs_define
class SendIn:
    """
    Attributes:
        external_ref (str):
        identity_id (str):
        text (str):
        answer_to (None | str | Unset):
    """

    external_ref: str
    identity_id: str
    text: str
    answer_to: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        external_ref = self.external_ref

        identity_id = self.identity_id

        text = self.text

        answer_to: None | str | Unset
        if isinstance(self.answer_to, Unset):
            answer_to = UNSET
        else:
            answer_to = self.answer_to

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "external_ref": external_ref,
                "identity_id": identity_id,
                "text": text,
            }
        )
        if answer_to is not UNSET:
            field_dict["answer_to"] = answer_to

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        external_ref = d.pop("external_ref")

        identity_id = d.pop("identity_id")

        text = d.pop("text")

        def _parse_answer_to(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        answer_to = _parse_answer_to(d.pop("answer_to", UNSET))

        send_in = cls(
            external_ref=external_ref,
            identity_id=identity_id,
            text=text,
            answer_to=answer_to,
        )

        send_in.additional_properties = d
        return send_in

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
