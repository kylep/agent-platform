from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.cron_entry_out import CronEntryOut
    from ..models.webhook_entry_out import WebhookEntryOut


T = TypeVar("T", bound="EntrypointsOut")


@_attrs_define
class EntrypointsOut:
    """
    Attributes:
        crons (list[CronEntryOut] | Unset):
        timezone (str | Unset):  Default: ''.
        topics (list[str] | Unset):
        webhooks (list[WebhookEntryOut] | Unset):
    """

    crons: list[CronEntryOut] | Unset = UNSET
    timezone: str | Unset = ""
    topics: list[str] | Unset = UNSET
    webhooks: list[WebhookEntryOut] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        crons: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.crons, Unset):
            crons = []
            for crons_item_data in self.crons:
                crons_item = crons_item_data.to_dict()
                crons.append(crons_item)

        timezone = self.timezone

        topics: list[str] | Unset = UNSET
        if not isinstance(self.topics, Unset):
            topics = self.topics

        webhooks: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.webhooks, Unset):
            webhooks = []
            for webhooks_item_data in self.webhooks:
                webhooks_item = webhooks_item_data.to_dict()
                webhooks.append(webhooks_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if crons is not UNSET:
            field_dict["crons"] = crons
        if timezone is not UNSET:
            field_dict["timezone"] = timezone
        if topics is not UNSET:
            field_dict["topics"] = topics
        if webhooks is not UNSET:
            field_dict["webhooks"] = webhooks

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.cron_entry_out import CronEntryOut
        from ..models.webhook_entry_out import WebhookEntryOut

        d = dict(src_dict)
        _crons = d.pop("crons", UNSET)
        crons: list[CronEntryOut] | Unset = UNSET
        if _crons is not UNSET:
            crons = []
            for crons_item_data in _crons:
                crons_item = CronEntryOut.from_dict(crons_item_data)

                crons.append(crons_item)

        timezone = d.pop("timezone", UNSET)

        topics = cast(list[str], d.pop("topics", UNSET))

        _webhooks = d.pop("webhooks", UNSET)
        webhooks: list[WebhookEntryOut] | Unset = UNSET
        if _webhooks is not UNSET:
            webhooks = []
            for webhooks_item_data in _webhooks:
                webhooks_item = WebhookEntryOut.from_dict(webhooks_item_data)

                webhooks.append(webhooks_item)

        entrypoints_out = cls(
            crons=crons,
            timezone=timezone,
            topics=topics,
            webhooks=webhooks,
        )

        entrypoints_out.additional_properties = d
        return entrypoints_out

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
