from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

T = TypeVar("T", bound="SecretVerify")


@_attrs_define
class SecretVerify:
    """
    Attributes:
        code (int | None):
        detail (str):
        name (str):
        status (str):
    """

    code: int | None
    detail: str
    name: str
    status: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        code: int | None
        code = self.code

        detail = self.detail

        name = self.name

        status = self.status

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "code": code,
                "detail": detail,
                "name": name,
                "status": status,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)

        def _parse_code(data: object) -> int | None:
            if data is None:
                return data
            return cast(int | None, data)

        code = _parse_code(d.pop("code"))

        detail = d.pop("detail")

        name = d.pop("name")

        status = d.pop("status")

        secret_verify = cls(
            code=code,
            detail=detail,
            name=name,
            status=status,
        )

        secret_verify.additional_properties = d
        return secret_verify

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
