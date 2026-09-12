from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

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
    """

    connector: str
    external_ref: str
    config: RelayBindingInConfig | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        connector = self.connector

        external_ref = self.external_ref

        config: dict[str, Any] | Unset = UNSET
        if not isinstance(self.config, Unset):
            config = self.config.to_dict()

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "connector": connector,
                "external_ref": external_ref,
            }
        )
        if config is not UNSET:
            field_dict["config"] = config

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

        relay_binding_in = cls(
            connector=connector,
            external_ref=external_ref,
            config=config,
        )

        return relay_binding_in
