from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.probe_in import ProbeIn
    from ..models.secret_key_in import SecretKeyIn


T = TypeVar("T", bound="SecretDeclareIn")


@_attrs_define
class SecretDeclareIn:
    """
    Attributes:
        name (str):
        description (str | Unset):  Default: ''.
        hint (str | Unset):  Default: ''.
        keys (list[SecretKeyIn] | Unset):
        probe (None | ProbeIn | Unset):
        required (bool | Unset):  Default: False.
    """

    name: str
    description: str | Unset = ""
    hint: str | Unset = ""
    keys: list[SecretKeyIn] | Unset = UNSET
    probe: None | ProbeIn | Unset = UNSET
    required: bool | Unset = False
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.probe_in import ProbeIn

        name = self.name

        description = self.description

        hint = self.hint

        keys: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.keys, Unset):
            keys = []
            for keys_item_data in self.keys:
                keys_item = keys_item_data.to_dict()
                keys.append(keys_item)

        probe: dict[str, Any] | None | Unset
        if isinstance(self.probe, Unset):
            probe = UNSET
        elif isinstance(self.probe, ProbeIn):
            probe = self.probe.to_dict()
        else:
            probe = self.probe

        required = self.required

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "name": name,
            }
        )
        if description is not UNSET:
            field_dict["description"] = description
        if hint is not UNSET:
            field_dict["hint"] = hint
        if keys is not UNSET:
            field_dict["keys"] = keys
        if probe is not UNSET:
            field_dict["probe"] = probe
        if required is not UNSET:
            field_dict["required"] = required

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.probe_in import ProbeIn
        from ..models.secret_key_in import SecretKeyIn

        d = dict(src_dict)
        name = d.pop("name")

        description = d.pop("description", UNSET)

        hint = d.pop("hint", UNSET)

        _keys = d.pop("keys", UNSET)
        keys: list[SecretKeyIn] | Unset = UNSET
        if _keys is not UNSET:
            keys = []
            for keys_item_data in _keys:
                keys_item = SecretKeyIn.from_dict(keys_item_data)

                keys.append(keys_item)

        def _parse_probe(data: object) -> None | ProbeIn | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                probe_type_0 = ProbeIn.from_dict(data)

                return probe_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | ProbeIn | Unset, data)

        probe = _parse_probe(d.pop("probe", UNSET))

        required = d.pop("required", UNSET)

        secret_declare_in = cls(
            name=name,
            description=description,
            hint=hint,
            keys=keys,
            probe=probe,
            required=required,
        )

        secret_declare_in.additional_properties = d
        return secret_declare_in

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
