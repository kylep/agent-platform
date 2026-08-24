from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

T = TypeVar("T", bound="AgentVersionRow")


@_attrs_define
class AgentVersionRow:
    """One entry of the append-only change log, without its snapshot.

    Attributes:
        changed_by (str):
        changed_via (str):
        created_at (None | str):
        version (int):
    """

    changed_by: str
    changed_via: str
    created_at: None | str
    version: int
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        changed_by = self.changed_by

        changed_via = self.changed_via

        created_at: None | str
        created_at = self.created_at

        version = self.version

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "changed_by": changed_by,
                "changed_via": changed_via,
                "created_at": created_at,
                "version": version,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        changed_by = d.pop("changed_by")

        changed_via = d.pop("changed_via")

        def _parse_created_at(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        created_at = _parse_created_at(d.pop("created_at"))

        version = d.pop("version")

        agent_version_row = cls(
            changed_by=changed_by,
            changed_via=changed_via,
            created_at=created_at,
            version=version,
        )

        agent_version_row.additional_properties = d
        return agent_version_row

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
