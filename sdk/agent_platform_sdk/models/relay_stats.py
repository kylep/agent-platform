from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

if TYPE_CHECKING:
    from ..models.relay_budget import RelayBudget
    from ..models.relay_settings import RelaySettings


T = TypeVar("T", bound="RelayStats")


@_attrs_define
class RelayStats:
    """
    Attributes:
        agent_messages_24h (int):
        budget (RelayBudget):
        invocations_24h (int):
        messages_24h (int):
        settings (RelaySettings): What the running platform is actually enforcing (docs/design/19).

            READ-ONLY, and here rather than behind a settings endpoint because these
            are environment settings: `Settings` is a pydantic-settings object read
            from AP_* at boot, with no runtime-mutation mechanism anywhere in the API
            to hang a toggle off. Reporting them beside the counters they govern at
            least means an operator reading "suppressed_24h: 40" can see the budget
            that suppressed them without going to read the Helm values.
        suppressed_24h (int):
    """

    agent_messages_24h: int
    budget: RelayBudget
    invocations_24h: int
    messages_24h: int
    settings: RelaySettings
    suppressed_24h: int
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        agent_messages_24h = self.agent_messages_24h

        budget = self.budget.to_dict()

        invocations_24h = self.invocations_24h

        messages_24h = self.messages_24h

        settings = self.settings.to_dict()

        suppressed_24h = self.suppressed_24h

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "agent_messages_24h": agent_messages_24h,
                "budget": budget,
                "invocations_24h": invocations_24h,
                "messages_24h": messages_24h,
                "settings": settings,
                "suppressed_24h": suppressed_24h,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.relay_budget import RelayBudget
        from ..models.relay_settings import RelaySettings

        d = dict(src_dict)
        agent_messages_24h = d.pop("agent_messages_24h")

        budget = RelayBudget.from_dict(d.pop("budget"))

        invocations_24h = d.pop("invocations_24h")

        messages_24h = d.pop("messages_24h")

        settings = RelaySettings.from_dict(d.pop("settings"))

        suppressed_24h = d.pop("suppressed_24h")

        relay_stats = cls(
            agent_messages_24h=agent_messages_24h,
            budget=budget,
            invocations_24h=invocations_24h,
            messages_24h=messages_24h,
            settings=settings,
            suppressed_24h=suppressed_24h,
        )

        relay_stats.additional_properties = d
        return relay_stats

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
