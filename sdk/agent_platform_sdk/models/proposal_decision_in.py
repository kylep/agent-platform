from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="ProposalDecisionIn")


@_attrs_define
class ProposalDecisionIn:
    """
    Attributes:
        request_id (str):
        digest (None | str | Unset):
        reason (str | Unset):  Default: ''.
    """

    request_id: str
    digest: None | str | Unset = UNSET
    reason: str | Unset = ""

    def to_dict(self) -> dict[str, Any]:
        request_id = self.request_id

        digest: None | str | Unset
        if isinstance(self.digest, Unset):
            digest = UNSET
        else:
            digest = self.digest

        reason = self.reason

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "request_id": request_id,
            }
        )
        if digest is not UNSET:
            field_dict["digest"] = digest
        if reason is not UNSET:
            field_dict["reason"] = reason

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        request_id = d.pop("request_id")

        def _parse_digest(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        digest = _parse_digest(d.pop("digest", UNSET))

        reason = d.pop("reason", UNSET)

        proposal_decision_in = cls(
            request_id=request_id,
            digest=digest,
            reason=reason,
        )

        return proposal_decision_in
