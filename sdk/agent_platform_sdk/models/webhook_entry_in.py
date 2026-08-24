from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from typing_extensions import Self

from ..types import UNSET, Unset

T = TypeVar("T", bound="WebhookEntryIn")


@_attrs_define
class WebhookEntryIn:
    """
    Attributes:
        path (str):
        auth (str | Unset):  Default: 'none'.
        secret_set (bool | Unset):  Default: False.
    """

    path: str
    auth: str | Unset = "none"
    secret_set: bool | Unset = False

    def to_dict(self) -> dict[str, Any]:
        path = self.path

        auth = self.auth

        secret_set = self.secret_set

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "path": path,
            }
        )
        if auth is not UNSET:
            field_dict["auth"] = auth
        if secret_set is not UNSET:
            field_dict["secret_set"] = secret_set

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        path = d.pop("path")

        auth = d.pop("auth", UNSET)

        secret_set = d.pop("secret_set", UNSET)

        webhook_entry_in = cls(
            path=path,
            auth=auth,
            secret_set=secret_set,
        )

        return webhook_entry_in
