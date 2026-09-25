from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..types import UNSET, Unset

T = TypeVar("T", bound="WebhookSecretState")


@_attrs_define
class WebhookSecretState:
    """What a secret write reports: the path it touched and whether one is now
    set. Never the secret.

        Attributes:
            agent (str):
            path (str):
            secret_set (bool):
            ok (bool | Unset):  Default: True.
    """

    agent: str
    path: str
    secret_set: bool
    ok: bool | Unset = True
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        agent = self.agent

        path = self.path

        secret_set = self.secret_set

        ok = self.ok

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "agent": agent,
                "path": path,
                "secret_set": secret_set,
            }
        )
        if ok is not UNSET:
            field_dict["ok"] = ok

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        agent = d.pop("agent")

        path = d.pop("path")

        secret_set = d.pop("secret_set")

        ok = d.pop("ok", UNSET)

        webhook_secret_state = cls(
            agent=agent,
            path=path,
            secret_set=secret_set,
            ok=ok,
        )

        webhook_secret_state.additional_properties = d
        return webhook_secret_state

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
