from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from typing_extensions import Self

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.relay_binding_in_config import RelayBindingInConfig


T = TypeVar("T", bound="RelayBindingIn")


@_attrs_define
class RelayBindingIn:
    """
    Attributes:
        connector (str):
        external_ref (str):
        config (RelayBindingInConfig | Unset):
        display_name (str | Unset):  Default: ''.
        external_kind (str | Unset):  Default: 'channel'.
        external_url (str | Unset):  Default: ''.
        identity_id (None | str | Unset):
        parent_external_ref (None | str | Unset):
    """

    connector: str
    external_ref: str
    config: RelayBindingInConfig | Unset = UNSET
    display_name: str | Unset = ""
    external_kind: str | Unset = "channel"
    external_url: str | Unset = ""
    identity_id: None | str | Unset = UNSET
    parent_external_ref: None | str | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        connector = self.connector

        external_ref = self.external_ref

        config: dict[str, Any] | Unset = UNSET
        if not isinstance(self.config, Unset):
            config = self.config.to_dict()

        display_name = self.display_name

        external_kind = self.external_kind

        external_url = self.external_url

        identity_id: None | str | Unset
        if isinstance(self.identity_id, Unset):
            identity_id = UNSET
        else:
            identity_id = self.identity_id

        parent_external_ref: None | str | Unset
        if isinstance(self.parent_external_ref, Unset):
            parent_external_ref = UNSET
        else:
            parent_external_ref = self.parent_external_ref

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "connector": connector,
                "external_ref": external_ref,
            }
        )
        if config is not UNSET:
            field_dict["config"] = config
        if display_name is not UNSET:
            field_dict["display_name"] = display_name
        if external_kind is not UNSET:
            field_dict["external_kind"] = external_kind
        if external_url is not UNSET:
            field_dict["external_url"] = external_url
        if identity_id is not UNSET:
            field_dict["identity_id"] = identity_id
        if parent_external_ref is not UNSET:
            field_dict["parent_external_ref"] = parent_external_ref

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.relay_binding_in_config import RelayBindingInConfig

        d = dict(src_dict)
        connector = d.pop("connector")

        external_ref = d.pop("external_ref")

        _config = d.pop("config", UNSET)
        config: RelayBindingInConfig | Unset
        if isinstance(_config, Unset):
            config = UNSET
        else:
            config = RelayBindingInConfig.from_dict(_config)

        display_name = d.pop("display_name", UNSET)

        external_kind = d.pop("external_kind", UNSET)

        external_url = d.pop("external_url", UNSET)

        def _parse_identity_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        identity_id = _parse_identity_id(d.pop("identity_id", UNSET))

        def _parse_parent_external_ref(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        parent_external_ref = _parse_parent_external_ref(
            d.pop("parent_external_ref", UNSET)
        )

        relay_binding_in = cls(
            connector=connector,
            external_ref=external_ref,
            config=config,
            display_name=display_name,
            external_kind=external_kind,
            external_url=external_url,
            identity_id=identity_id,
            parent_external_ref=parent_external_ref,
        )

        return relay_binding_in
