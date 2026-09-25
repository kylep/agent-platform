from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..types import UNSET, Unset

T = TypeVar("T", bound="ConversationTurn")


@_attrs_define
class ConversationTurn:
    """
    Attributes:
        created_at (None | str):
        result (None | str):
        run_id (str):
        state (str):
        user_message (None | str):
        sender (str | Unset):  Default: 'unknown'.
    """

    created_at: None | str
    result: None | str
    run_id: str
    state: str
    user_message: None | str
    sender: str | Unset = "unknown"
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        created_at: None | str
        created_at = self.created_at

        result: None | str
        result = self.result

        run_id = self.run_id

        state = self.state

        user_message: None | str
        user_message = self.user_message

        sender = self.sender

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "created_at": created_at,
                "result": result,
                "run_id": run_id,
                "state": state,
                "user_message": user_message,
            }
        )
        if sender is not UNSET:
            field_dict["sender"] = sender

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)

        def _parse_created_at(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        created_at = _parse_created_at(d.pop("created_at"))

        def _parse_result(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        result = _parse_result(d.pop("result"))

        run_id = d.pop("run_id")

        state = d.pop("state")

        def _parse_user_message(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        user_message = _parse_user_message(d.pop("user_message"))

        sender = d.pop("sender", UNSET)

        conversation_turn = cls(
            created_at=created_at,
            result=result,
            run_id=run_id,
            state=state,
            user_message=user_message,
            sender=sender,
        )

        conversation_turn.additional_properties = d
        return conversation_turn

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
