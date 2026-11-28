from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="PlaySectionIn")


@_attrs_define
class PlaySectionIn:
    """
    Attributes:
        agent (str):
        section (str):
        changed_by (str | Unset):  Default: 'system:village'.
        expected_version (int | None | Unset):
    """

    agent: str
    section: str
    changed_by: str | Unset = "system:village"
    expected_version: int | None | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        agent = self.agent

        section = self.section

        changed_by = self.changed_by

        expected_version: int | None | Unset
        if isinstance(self.expected_version, Unset):
            expected_version = UNSET
        else:
            expected_version = self.expected_version

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "agent": agent,
                "section": section,
            }
        )
        if changed_by is not UNSET:
            field_dict["changed_by"] = changed_by
        if expected_version is not UNSET:
            field_dict["expected_version"] = expected_version

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        agent = d.pop("agent")

        section = d.pop("section")

        changed_by = d.pop("changed_by", UNSET)

        def _parse_expected_version(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        expected_version = _parse_expected_version(d.pop("expected_version", UNSET))

        play_section_in = cls(
            agent=agent,
            section=section,
            changed_by=changed_by,
            expected_version=expected_version,
        )

        return play_section_in
