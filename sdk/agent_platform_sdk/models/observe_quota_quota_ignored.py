from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..types import UNSET, Unset

T = TypeVar("T", bound="ObserveQuotaQuotaIgnored")


@_attrs_define
class ObserveQuotaQuotaIgnored:
    """The answer to a report that said nothing about usage.

    Most responses the proxy relays carry no usage header at all, so this is
    the COMMON outcome, not an error — writing those would overwrite a real
    snapshot with nulls. It is a 200 with a body rather than a 204 because the
    proxy is njs, whose `ngx.fetch` never settles its promise on a bodyless
    204: the request would hang until nginx timed it out, once per relayed
    Anthropic call. It deliberately carries no snapshot — the hot path must not
    cost a database read to say "nothing to record".

        Attributes:
            ignored (bool | Unset):  Default: True.
    """

    ignored: bool | Unset = True
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        ignored = self.ignored

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if ignored is not UNSET:
            field_dict["ignored"] = ignored

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        ignored = d.pop("ignored", UNSET)

        observe_quota_quota_ignored = cls(
            ignored=ignored,
        )

        observe_quota_quota_ignored.additional_properties = d
        return observe_quota_quota_ignored

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
