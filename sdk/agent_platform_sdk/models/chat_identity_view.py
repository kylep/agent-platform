from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.chat_identity_view_secret_refs import ChatIdentityViewSecretRefs


T = TypeVar("T", bound="ChatIdentityView")


@_attrs_define
class ChatIdentityView:
    """
    Attributes:
        bound_routes (int):
        configured (bool):
        connector (str):
        display_name (str):
        id (str):
        secret_refs (ChatIdentityViewSecretRefs):
        status (str):
        access_expires_at (datetime.datetime | None | Unset):
        connected (bool | Unset):  Default: False.
        owner_agent (None | str | Unset):
        ownership_generation (int | Unset):  Default: 0.
    """

    bound_routes: int
    configured: bool
    connector: str
    display_name: str
    id: str
    secret_refs: ChatIdentityViewSecretRefs
    status: str
    access_expires_at: datetime.datetime | None | Unset = UNSET
    connected: bool | Unset = False
    owner_agent: None | str | Unset = UNSET
    ownership_generation: int | Unset = 0
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        bound_routes = self.bound_routes

        configured = self.configured

        connector = self.connector

        display_name = self.display_name

        id = self.id

        secret_refs = self.secret_refs.to_dict()

        status = self.status

        access_expires_at: None | str | Unset
        if isinstance(self.access_expires_at, Unset):
            access_expires_at = UNSET
        elif isinstance(self.access_expires_at, datetime.datetime):
            access_expires_at = self.access_expires_at.isoformat()
        else:
            access_expires_at = self.access_expires_at

        connected = self.connected

        owner_agent: None | str | Unset
        if isinstance(self.owner_agent, Unset):
            owner_agent = UNSET
        else:
            owner_agent = self.owner_agent

        ownership_generation = self.ownership_generation

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "bound_routes": bound_routes,
                "configured": configured,
                "connector": connector,
                "display_name": display_name,
                "id": id,
                "secret_refs": secret_refs,
                "status": status,
            }
        )
        if access_expires_at is not UNSET:
            field_dict["access_expires_at"] = access_expires_at
        if connected is not UNSET:
            field_dict["connected"] = connected
        if owner_agent is not UNSET:
            field_dict["owner_agent"] = owner_agent
        if ownership_generation is not UNSET:
            field_dict["ownership_generation"] = ownership_generation

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.chat_identity_view_secret_refs import ChatIdentityViewSecretRefs

        d = dict(src_dict)
        bound_routes = d.pop("bound_routes")

        configured = d.pop("configured")

        connector = d.pop("connector")

        display_name = d.pop("display_name")

        id = d.pop("id")

        secret_refs = ChatIdentityViewSecretRefs.from_dict(d.pop("secret_refs"))

        status = d.pop("status")

        def _parse_access_expires_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                access_expires_at_type_0 = datetime.datetime.fromisoformat(data)

                return access_expires_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None | Unset, data)

        access_expires_at = _parse_access_expires_at(d.pop("access_expires_at", UNSET))

        connected = d.pop("connected", UNSET)

        def _parse_owner_agent(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        owner_agent = _parse_owner_agent(d.pop("owner_agent", UNSET))

        ownership_generation = d.pop("ownership_generation", UNSET)

        chat_identity_view = cls(
            bound_routes=bound_routes,
            configured=configured,
            connector=connector,
            display_name=display_name,
            id=id,
            secret_refs=secret_refs,
            status=status,
            access_expires_at=access_expires_at,
            connected=connected,
            owner_agent=owner_agent,
            ownership_generation=ownership_generation,
        )

        chat_identity_view.additional_properties = d
        return chat_identity_view

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
