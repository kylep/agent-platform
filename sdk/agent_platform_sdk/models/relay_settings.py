from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

T = TypeVar("T", bound="RelaySettings")


@_attrs_define
class RelaySettings:
    """What the running platform is actually enforcing (docs/design/19).

    READ-ONLY, and here rather than behind a settings endpoint because these
    are environment settings: `Settings` is a pydantic-settings object read
    from AP_* at boot, with no runtime-mutation mechanism anywhere in the API
    to hang a toggle off. Reporting them beside the counters they govern at
    least means an operator reading "suppressed_24h: 40" can see the budget
    that suppressed them without going to read the Helm values.

        Attributes:
            channel_per_hour (int):
            cooldown_seconds (int):
            default_grant (bool):
            global_per_hour (int):
            max_hops (int):
    """

    channel_per_hour: int
    cooldown_seconds: int
    default_grant: bool
    global_per_hour: int
    max_hops: int
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        channel_per_hour = self.channel_per_hour

        cooldown_seconds = self.cooldown_seconds

        default_grant = self.default_grant

        global_per_hour = self.global_per_hour

        max_hops = self.max_hops

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "channel_per_hour": channel_per_hour,
                "cooldown_seconds": cooldown_seconds,
                "default_grant": default_grant,
                "global_per_hour": global_per_hour,
                "max_hops": max_hops,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        channel_per_hour = d.pop("channel_per_hour")

        cooldown_seconds = d.pop("cooldown_seconds")

        default_grant = d.pop("default_grant")

        global_per_hour = d.pop("global_per_hour")

        max_hops = d.pop("max_hops")

        relay_settings = cls(
            channel_per_hour=channel_per_hour,
            cooldown_seconds=cooldown_seconds,
            default_grant=default_grant,
            global_per_hour=global_per_hour,
            max_hops=max_hops,
        )

        relay_settings.additional_properties = d
        return relay_settings

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
