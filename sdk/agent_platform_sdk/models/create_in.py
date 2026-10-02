from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="CreateIn")


@_attrs_define
class CreateIn:
    """
    Attributes:
        name (str):
        request_id (str):
        description (str | Unset):  Default: ''.
        timezone (str | Unset):  Default: 'UTC'.
    """

    name: str
    request_id: str
    description: str | Unset = ""
    timezone: str | Unset = "UTC"

    def to_dict(self) -> dict[str, Any]:
        name = self.name

        request_id = self.request_id

        description = self.description

        timezone = self.timezone

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "name": name,
                "request_id": request_id,
            }
        )
        if description is not UNSET:
            field_dict["description"] = description
        if timezone is not UNSET:
            field_dict["timezone"] = timezone

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        name = d.pop("name")

        request_id = d.pop("request_id")

        description = d.pop("description", UNSET)

        timezone = d.pop("timezone", UNSET)

        create_in = cls(
            name=name,
            request_id=request_id,
            description=description,
            timezone=timezone,
        )

        return create_in
