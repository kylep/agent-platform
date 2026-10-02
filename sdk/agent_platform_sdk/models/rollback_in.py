from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="RollbackIn")


@_attrs_define
class RollbackIn:
    """
    Attributes:
        app (str): App id or name
        expected_approved_version (int | None):
        request_id (str):
        to_version (int):
        reason (str | Unset):  Default: ''.
    """

    app: str
    expected_approved_version: int | None
    request_id: str
    to_version: int
    reason: str | Unset = ""

    def to_dict(self) -> dict[str, Any]:
        app = self.app

        expected_approved_version: int | None
        expected_approved_version = self.expected_approved_version

        request_id = self.request_id

        to_version = self.to_version

        reason = self.reason

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "app": app,
                "expected_approved_version": expected_approved_version,
                "request_id": request_id,
                "to_version": to_version,
            }
        )
        if reason is not UNSET:
            field_dict["reason"] = reason

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        app = d.pop("app")

        def _parse_expected_approved_version(data: object) -> int | None:
            if data is None:
                return data
            return cast(int | None, data)

        expected_approved_version = _parse_expected_approved_version(
            d.pop("expected_approved_version")
        )

        request_id = d.pop("request_id")

        to_version = d.pop("to_version")

        reason = d.pop("reason", UNSET)

        rollback_in = cls(
            app=app,
            expected_approved_version=expected_approved_version,
            request_id=request_id,
            to_version=to_version,
            reason=reason,
        )

        return rollback_in
