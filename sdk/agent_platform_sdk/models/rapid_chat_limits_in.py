from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="RapidChatLimitsIn")


@_attrs_define
class RapidChatLimitsIn:
    """
    Attributes:
        claude_5h_max_pct (int | Unset):  Default: 95.
        claude_7d_max_pct (int | Unset):  Default: 40.
        codex_7d_max_pct (int | Unset):  Default: 40.
    """

    claude_5h_max_pct: int | Unset = 95
    claude_7d_max_pct: int | Unset = 40
    codex_7d_max_pct: int | Unset = 40

    def to_dict(self) -> dict[str, Any]:
        claude_5h_max_pct = self.claude_5h_max_pct

        claude_7d_max_pct = self.claude_7d_max_pct

        codex_7d_max_pct = self.codex_7d_max_pct

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if claude_5h_max_pct is not UNSET:
            field_dict["claude_5h_max_pct"] = claude_5h_max_pct
        if claude_7d_max_pct is not UNSET:
            field_dict["claude_7d_max_pct"] = claude_7d_max_pct
        if codex_7d_max_pct is not UNSET:
            field_dict["codex_7d_max_pct"] = codex_7d_max_pct

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        claude_5h_max_pct = d.pop("claude_5h_max_pct", UNSET)

        claude_7d_max_pct = d.pop("claude_7d_max_pct", UNSET)

        codex_7d_max_pct = d.pop("codex_7d_max_pct", UNSET)

        rapid_chat_limits_in = cls(
            claude_5h_max_pct=claude_5h_max_pct,
            claude_7d_max_pct=claude_7d_max_pct,
            codex_7d_max_pct=codex_7d_max_pct,
        )

        return rapid_chat_limits_in
