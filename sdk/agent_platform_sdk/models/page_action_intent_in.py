from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.page_action_intent_in_values import PageActionIntentInValues


T = TypeVar("T", bound="PageActionIntentIn")


@_attrs_define
class PageActionIntentIn:
    """
    Attributes:
        template (str):
        record_id (None | str | Unset):
        values (PageActionIntentInValues | Unset):
    """

    template: str
    record_id: None | str | Unset = UNSET
    values: PageActionIntentInValues | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        template = self.template

        record_id: None | str | Unset
        if isinstance(self.record_id, Unset):
            record_id = UNSET
        else:
            record_id = self.record_id

        values: dict[str, Any] | Unset = UNSET
        if not isinstance(self.values, Unset):
            values = self.values.to_dict()

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "template": template,
            }
        )
        if record_id is not UNSET:
            field_dict["record_id"] = record_id
        if values is not UNSET:
            field_dict["values"] = values

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.page_action_intent_in_values import PageActionIntentInValues

        d = dict(src_dict)
        template = d.pop("template")

        def _parse_record_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        record_id = _parse_record_id(d.pop("record_id", UNSET))

        _values = d.pop("values", UNSET)
        values: PageActionIntentInValues | Unset
        if isinstance(_values, Unset):
            values = UNSET
        else:
            values = PageActionIntentInValues.from_dict(_values)

        page_action_intent_in = cls(
            template=template,
            record_id=record_id,
            values=values,
        )

        return page_action_intent_in
