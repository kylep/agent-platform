from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.webhook_entry import WebhookEntry


T = TypeVar("T", bound="Entrypoints")


@_attrs_define
class Entrypoints:
    """agents/<name>/entrypoints.yaml — the agent's durable, defining triggers
    (docs/design/10): cron fires, inbound webhook paths (POST
    /api/webhooks/<path> only works for a declared path), and kafka topic
    subscriptions (reserved). Distinct from DB Jobs, which are ad-hoc UI
    experiments — history, not config.

        Attributes:
            cron (list[str] | Unset):
            kafka (list[str] | Unset):
            webhooks (list[WebhookEntry] | Unset):
    """

    cron: list[str] | Unset = UNSET
    kafka: list[str] | Unset = UNSET
    webhooks: list[WebhookEntry] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        cron: list[str] | Unset = UNSET
        if not isinstance(self.cron, Unset):
            cron = self.cron

        kafka: list[str] | Unset = UNSET
        if not isinstance(self.kafka, Unset):
            kafka = self.kafka

        webhooks: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.webhooks, Unset):
            webhooks = []
            for webhooks_item_data in self.webhooks:
                webhooks_item = webhooks_item_data.to_dict()
                webhooks.append(webhooks_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if cron is not UNSET:
            field_dict["cron"] = cron
        if kafka is not UNSET:
            field_dict["kafka"] = kafka
        if webhooks is not UNSET:
            field_dict["webhooks"] = webhooks

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.webhook_entry import WebhookEntry

        d = dict(src_dict)
        cron = cast(list[str], d.pop("cron", UNSET))

        kafka = cast(list[str], d.pop("kafka", UNSET))

        _webhooks = d.pop("webhooks", UNSET)
        webhooks: list[WebhookEntry] | Unset = UNSET
        if _webhooks is not UNSET:
            webhooks = []
            for webhooks_item_data in _webhooks:
                webhooks_item = WebhookEntry.from_dict(webhooks_item_data)

                webhooks.append(webhooks_item)

        entrypoints = cls(
            cron=cron,
            kafka=kafka,
            webhooks=webhooks,
        )

        entrypoints.additional_properties = d
        return entrypoints

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
