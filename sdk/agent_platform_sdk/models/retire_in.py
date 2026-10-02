from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="RetireIn")


@_attrs_define
class RetireIn:
    """
    Attributes:
        app (str): App id or name
        request_id (str):
        reason (str | Unset):  Default: ''.
    """

    app: str
    request_id: str
    reason: str | Unset = ""

    def to_dict(self) -> dict[str, Any]:
        app = self.app

        request_id = self.request_id

        reason = self.reason

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "app": app,
                "request_id": request_id,
            }
        )
        if reason is not UNSET:
            field_dict["reason"] = reason

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        app = d.pop("app")

        request_id = d.pop("request_id")

        reason = d.pop("reason", UNSET)

        retire_in = cls(
            app=app,
            request_id=request_id,
            reason=reason,
        )

        return retire_in
