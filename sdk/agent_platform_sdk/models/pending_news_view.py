from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

T = TypeVar("T", bound="PendingNewsView")


@_attrs_define
class PendingNewsView:
    """
    Attributes:
        channel (str):
        created_at (None | str):
        date (str):
        id (str):
        item_count (int):
        post_text (str):
        run_id (None | str):
        status (str):
    """

    channel: str
    created_at: None | str
    date: str
    id: str
    item_count: int
    post_text: str
    run_id: None | str
    status: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        channel = self.channel

        created_at: None | str
        created_at = self.created_at

        date = self.date

        id = self.id

        item_count = self.item_count

        post_text = self.post_text

        run_id: None | str
        run_id = self.run_id

        status = self.status

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "channel": channel,
                "created_at": created_at,
                "date": date,
                "id": id,
                "item_count": item_count,
                "post_text": post_text,
                "run_id": run_id,
                "status": status,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        channel = d.pop("channel")

        def _parse_created_at(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        created_at = _parse_created_at(d.pop("created_at"))

        date = d.pop("date")

        id = d.pop("id")

        item_count = d.pop("item_count")

        post_text = d.pop("post_text")

        def _parse_run_id(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        run_id = _parse_run_id(d.pop("run_id"))

        status = d.pop("status")

        pending_news_view = cls(
            channel=channel,
            created_at=created_at,
            date=date,
            id=id,
            item_count=item_count,
            post_text=post_text,
            run_id=run_id,
            status=status,
        )

        pending_news_view.additional_properties = d
        return pending_news_view

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
