from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.proposal_in_action import ProposalInAction
from ..types import UNSET, Unset

T = TypeVar("T", bound="ProposalIn")


@_attrs_define
class ProposalIn:
    """
    Attributes:
        action (ProposalInAction):
        proposal_id (str):
        request_id (None | str | Unset):
    """

    action: ProposalInAction
    proposal_id: str
    request_id: None | str | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        action = self.action.value

        proposal_id = self.proposal_id

        request_id: None | str | Unset
        if isinstance(self.request_id, Unset):
            request_id = UNSET
        else:
            request_id = self.request_id

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "action": action,
                "proposal_id": proposal_id,
            }
        )
        if request_id is not UNSET:
            field_dict["request_id"] = request_id

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        action = ProposalInAction(d.pop("action"))

        proposal_id = d.pop("proposal_id")

        def _parse_request_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        request_id = _parse_request_id(d.pop("request_id", UNSET))

        proposal_in = cls(
            action=action,
            proposal_id=proposal_id,
            request_id=request_id,
        )

        return proposal_in
