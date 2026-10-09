from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.watch_turn_view import WatchTurnView


T = TypeVar("T", bound="WatchRoundView")


@_attrs_define
class WatchRoundView:
    """
    Attributes:
        anchor_message_id (str):
        closed_at (None | str):
        created_at (str):
        last_message_id (str):
        round_id (str):
        state (str):
        turns (list[WatchTurnView]):
    """

    anchor_message_id: str
    closed_at: None | str
    created_at: str
    last_message_id: str
    round_id: str
    state: str
    turns: list[WatchTurnView]
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        anchor_message_id = self.anchor_message_id

        closed_at: None | str
        closed_at = self.closed_at

        created_at = self.created_at

        last_message_id = self.last_message_id

        round_id = self.round_id

        state = self.state

        turns = []
        for turns_item_data in self.turns:
            turns_item = turns_item_data.to_dict()
            turns.append(turns_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "anchor_message_id": anchor_message_id,
                "closed_at": closed_at,
                "created_at": created_at,
                "last_message_id": last_message_id,
                "round_id": round_id,
                "state": state,
                "turns": turns,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.watch_turn_view import WatchTurnView

        d = dict(src_dict)
        anchor_message_id = d.pop("anchor_message_id")

        def _parse_closed_at(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        closed_at = _parse_closed_at(d.pop("closed_at"))

        created_at = d.pop("created_at")

        last_message_id = d.pop("last_message_id")

        round_id = d.pop("round_id")

        state = d.pop("state")

        turns = []
        _turns = d.pop("turns")
        for turns_item_data in _turns:
            turns_item = WatchTurnView.from_dict(turns_item_data)

            turns.append(turns_item)

        watch_round_view = cls(
            anchor_message_id=anchor_message_id,
            closed_at=closed_at,
            created_at=created_at,
            last_message_id=last_message_id,
            round_id=round_id,
            state=state,
            turns=turns,
        )

        watch_round_view.additional_properties = d
        return watch_round_view

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
