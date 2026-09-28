from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="ReceiptIn")


@_attrs_define
class ReceiptIn:
    """
    Attributes:
        claim_token (str):
        index (int | None | Unset):
        outcome (None | str | Unset):
        provider_message_id (None | str | Unset):
    """

    claim_token: str
    index: int | None | Unset = UNSET
    outcome: None | str | Unset = UNSET
    provider_message_id: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        claim_token = self.claim_token

        index: int | None | Unset
        if isinstance(self.index, Unset):
            index = UNSET
        else:
            index = self.index

        outcome: None | str | Unset
        if isinstance(self.outcome, Unset):
            outcome = UNSET
        else:
            outcome = self.outcome

        provider_message_id: None | str | Unset
        if isinstance(self.provider_message_id, Unset):
            provider_message_id = UNSET
        else:
            provider_message_id = self.provider_message_id

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "claim_token": claim_token,
            }
        )
        if index is not UNSET:
            field_dict["index"] = index
        if outcome is not UNSET:
            field_dict["outcome"] = outcome
        if provider_message_id is not UNSET:
            field_dict["provider_message_id"] = provider_message_id

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        claim_token = d.pop("claim_token")

        def _parse_index(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        index = _parse_index(d.pop("index", UNSET))

        def _parse_outcome(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        outcome = _parse_outcome(d.pop("outcome", UNSET))

        def _parse_provider_message_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        provider_message_id = _parse_provider_message_id(
            d.pop("provider_message_id", UNSET)
        )

        receipt_in = cls(
            claim_token=claim_token,
            index=index,
            outcome=outcome,
            provider_message_id=provider_message_id,
        )

        receipt_in.additional_properties = d
        return receipt_in

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
