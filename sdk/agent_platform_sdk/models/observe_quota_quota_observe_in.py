from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.observe_quota_quota_observe_in_headers import (
        ObserveQuotaQuotaObserveInHeaders,
    )


T = TypeVar("T", bound="ObserveQuotaQuotaObserveIn")


@_attrs_define
class ObserveQuotaQuotaObserveIn:
    """What the claude-proxy reports (docs/design/22): the response headers it
    just relayed, verbatim. Extra keys are tolerated — the proxy is a shell
    script's worth of nginx/njs and the contract has to survive it growing a
    field — and a body carrying no usage header at all is ignored, not an
    error, because most responses say nothing about usage.

    The bounds are here rather than left to the store because this is the one
    door on the platform that a session cannot open and an API key cannot
    open: whatever reaches it has already been trusted on a shared secret, so
    the shape is the only thing left to check.

        Attributes:
            headers (ObserveQuotaQuotaObserveInHeaders | Unset):
            observed_at (None | str | Unset):
            status (int | None | Unset):
    """

    headers: ObserveQuotaQuotaObserveInHeaders | Unset = UNSET
    observed_at: None | str | Unset = UNSET
    status: int | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        headers: dict[str, Any] | Unset = UNSET
        if not isinstance(self.headers, Unset):
            headers = self.headers.to_dict()

        observed_at: None | str | Unset
        if isinstance(self.observed_at, Unset):
            observed_at = UNSET
        else:
            observed_at = self.observed_at

        status: int | None | Unset
        if isinstance(self.status, Unset):
            status = UNSET
        else:
            status = self.status

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if headers is not UNSET:
            field_dict["headers"] = headers
        if observed_at is not UNSET:
            field_dict["observed_at"] = observed_at
        if status is not UNSET:
            field_dict["status"] = status

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.observe_quota_quota_observe_in_headers import (
            ObserveQuotaQuotaObserveInHeaders,
        )

        d = dict(src_dict)
        _headers = d.pop("headers", UNSET)
        headers: ObserveQuotaQuotaObserveInHeaders | Unset
        if isinstance(_headers, Unset):
            headers = UNSET
        else:
            headers = ObserveQuotaQuotaObserveInHeaders.from_dict(_headers)

        def _parse_observed_at(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        observed_at = _parse_observed_at(d.pop("observed_at", UNSET))

        def _parse_status(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        status = _parse_status(d.pop("status", UNSET))

        observe_quota_quota_observe_in = cls(
            headers=headers,
            observed_at=observed_at,
            status=status,
        )

        observe_quota_quota_observe_in.additional_properties = d
        return observe_quota_quota_observe_in

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
