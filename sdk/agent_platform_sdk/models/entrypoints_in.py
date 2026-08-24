from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from typing_extensions import Self

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.cron_entry_in import CronEntryIn
    from ..models.webhook_entry_in import WebhookEntryIn


T = TypeVar("T", bound="EntrypointsIn")


@_attrs_define
class EntrypointsIn:
    """
    Attributes:
        crons (list[CronEntryIn] | Unset):
        timezone (str | Unset):  Default: ''.
        topics (list[str] | Unset):
        webhooks (list[WebhookEntryIn] | Unset):
    """

    crons: list[CronEntryIn] | Unset = UNSET
    timezone: str | Unset = ""
    topics: list[str] | Unset = UNSET
    webhooks: list[WebhookEntryIn] | Unset = UNSET

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
        from ..models.cron_entry_in import CronEntryIn
        from ..models.webhook_entry_in import WebhookEntryIn

        d = dict(src_dict)
        _crons = d.pop("crons", UNSET)
        crons: list[CronEntryIn] | Unset = UNSET
        if _crons is not UNSET:
            crons = []
            for crons_item_data in _crons:
                crons_item = CronEntryIn.from_dict(crons_item_data)

                crons.append(crons_item)

        timezone = d.pop("timezone", UNSET)

        topics = cast(list[str], d.pop("topics", UNSET))

        _webhooks = d.pop("webhooks", UNSET)
        webhooks: list[WebhookEntryIn] | Unset = UNSET
        if _webhooks is not UNSET:
            webhooks = []
            for webhooks_item_data in _webhooks:
                webhooks_item = WebhookEntryIn.from_dict(webhooks_item_data)

                webhooks.append(webhooks_item)

        entrypoints_in = cls(
            crons=crons,
            timezone=timezone,
            topics=topics,
            webhooks=webhooks,
        )

        return entrypoints_in
