from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="BackupConnectionIn")


@_attrs_define
class BackupConnectionIn:
    """
    Attributes:
        age_recipient (str):
        bucket (str):
        prefix (str | Unset):  Default: 'agent-platform'.
        service_account_json (None | str | Unset):
    """

    age_recipient: str
    bucket: str
    prefix: str | Unset = "agent-platform"
    service_account_json: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        age_recipient = self.age_recipient

        bucket = self.bucket

        prefix = self.prefix

        service_account_json: None | str | Unset
        if isinstance(self.service_account_json, Unset):
            service_account_json = UNSET
        else:
            service_account_json = self.service_account_json

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "age_recipient": age_recipient,
                "bucket": bucket,
            }
        )
        if prefix is not UNSET:
            field_dict["prefix"] = prefix
        if service_account_json is not UNSET:
            field_dict["service_account_json"] = service_account_json

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        age_recipient = d.pop("age_recipient")

        bucket = d.pop("bucket")

        prefix = d.pop("prefix", UNSET)

        def _parse_service_account_json(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        service_account_json = _parse_service_account_json(
            d.pop("service_account_json", UNSET)
        )

        backup_connection_in = cls(
            age_recipient=age_recipient,
            bucket=bucket,
            prefix=prefix,
            service_account_json=service_account_json,
        )

        backup_connection_in.additional_properties = d
        return backup_connection_in

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
