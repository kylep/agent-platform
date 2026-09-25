from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

T = TypeVar("T", bound="SecretAccessView")


@_attrs_define
class SecretAccessView:
    """
    Attributes:
        agent (str):
        granted_at (None | str):
        id (str):
        run_id (str):
        secret (str):
    """

    agent: str
    granted_at: None | str
    id: str
    run_id: str
    secret: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        agent = self.agent

        granted_at: None | str
        granted_at = self.granted_at

        id = self.id

        run_id = self.run_id

        secret = self.secret

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "agent": agent,
                "granted_at": granted_at,
                "id": id,
                "run_id": run_id,
                "secret": secret,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        agent = d.pop("agent")

        def _parse_granted_at(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        granted_at = _parse_granted_at(d.pop("granted_at"))

        id = d.pop("id")

        run_id = d.pop("run_id")

        secret = d.pop("secret")

        secret_access_view = cls(
            agent=agent,
            granted_at=granted_at,
            id=id,
            run_id=run_id,
            secret=secret,
        )

        secret_access_view.additional_properties = d
        return secret_access_view

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
