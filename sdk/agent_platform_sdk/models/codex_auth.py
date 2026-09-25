from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..types import UNSET, Unset

T = TypeVar("T", bound="CodexAuth")


@_attrs_define
class CodexAuth:
    """
    Attributes:
        auth_json (str):
        sha256 (str | Unset):  Default: ''.
    """

    auth_json: str
    sha256: str | Unset = ""
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        auth_json = self.auth_json

        sha256 = self.sha256

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "auth_json": auth_json,
            }
        )
        if sha256 is not UNSET:
            field_dict["sha256"] = sha256

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        auth_json = d.pop("auth_json")

        sha256 = d.pop("sha256", UNSET)

        codex_auth = cls(
            auth_json=auth_json,
            sha256=sha256,
        )

        codex_auth.additional_properties = d
        return codex_auth

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
