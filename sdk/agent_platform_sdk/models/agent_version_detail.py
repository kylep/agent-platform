from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

if TYPE_CHECKING:
    from ..models.agent_version_detail_snapshot import AgentVersionDetailSnapshot


T = TypeVar("T", bound="AgentVersionDetail")


@_attrs_define
class AgentVersionDetail:
    """
    Attributes:
        changed_by (str):
        changed_via (str):
        created_at (None | str):
        snapshot (AgentVersionDetailSnapshot):
        version (int):
    """

    changed_by: str
    changed_via: str
    created_at: None | str
    snapshot: AgentVersionDetailSnapshot
    version: int
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        changed_by = self.changed_by

        changed_via = self.changed_via

        created_at: None | str
        created_at = self.created_at

        snapshot = self.snapshot.to_dict()

        version = self.version

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "changed_by": changed_by,
                "changed_via": changed_via,
                "created_at": created_at,
                "snapshot": snapshot,
                "version": version,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.agent_version_detail_snapshot import AgentVersionDetailSnapshot

        d = dict(src_dict)
        changed_by = d.pop("changed_by")

        changed_via = d.pop("changed_via")

        def _parse_created_at(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        created_at = _parse_created_at(d.pop("created_at"))

        snapshot = AgentVersionDetailSnapshot.from_dict(d.pop("snapshot"))

        version = d.pop("version")

        agent_version_detail = cls(
            changed_by=changed_by,
            changed_via=changed_via,
            created_at=created_at,
            snapshot=snapshot,
            version=version,
        )

        agent_version_detail.additional_properties = d
        return agent_version_detail

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
