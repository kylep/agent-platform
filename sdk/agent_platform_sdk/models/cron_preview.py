from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..types import UNSET, Unset

T = TypeVar("T", bound="CronPreview")


@_attrs_define
class CronPreview:
    """What a cron expression means and when it will next fire.

    A validation feed, not a request that can fail: an expression the operator
    is still typing is answered 200 with `error` set, because a 4xx per
    keystroke is noise in the console and in the network log. `english` and
    `next` are empty exactly when `error` is set.

        Attributes:
            english (str | Unset):  Default: ''.
            error (None | str | Unset):
            next_ (list[datetime.datetime] | Unset):
    """

    english: str | Unset = ""
    error: None | str | Unset = UNSET
    next_: list[datetime.datetime] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        english = self.english

        error: None | str | Unset
        if isinstance(self.error, Unset):
            error = UNSET
        else:
            error = self.error

        next_: list[str] | Unset = UNSET
        if not isinstance(self.next_, Unset):
            next_ = []
            for next_item_data in self.next_:
                next_item = next_item_data.isoformat()
                next_.append(next_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if english is not UNSET:
            field_dict["english"] = english
        if error is not UNSET:
            field_dict["error"] = error
        if next_ is not UNSET:
            field_dict["next"] = next_

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        english = d.pop("english", UNSET)

        def _parse_error(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        error = _parse_error(d.pop("error", UNSET))

        _next_ = d.pop("next", UNSET)
        next_: list[datetime.datetime] | Unset = UNSET
        if _next_ is not UNSET:
            next_ = []
            for next_item_data in _next_:
                next_item = datetime.datetime.fromisoformat(next_item_data)

                next_.append(next_item)

        cron_preview = cls(
            english=english,
            error=error,
            next_=next_,
        )

        cron_preview.additional_properties = d
        return cron_preview

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
