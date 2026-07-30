from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.manifest import Manifest


T = TypeVar("T", bound="AgentInfo")


@_attrs_define
class AgentInfo:
    """
    Attributes:
        agent_md (str):
        manifest (Manifest | None):
        name (str):
        error (None | str | Unset):
    """

    agent_md: str
    manifest: Manifest | None
    name: str
    error: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.manifest import Manifest

        agent_md = self.agent_md

        manifest: dict[str, Any] | None
        if isinstance(self.manifest, Manifest):
            manifest = self.manifest.to_dict()
        else:
            manifest = self.manifest

        name = self.name

        error: None | str | Unset
        if isinstance(self.error, Unset):
            error = UNSET
        else:
            error = self.error

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "agent_md": agent_md,
                "manifest": manifest,
                "name": name,
            }
        )
        if error is not UNSET:
            field_dict["error"] = error

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.manifest import Manifest

        d = dict(src_dict)
        agent_md = d.pop("agent_md")

        def _parse_manifest(data: object) -> Manifest | None:
            if data is None:
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                manifest_type_0 = Manifest.from_dict(data)

                return manifest_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(Manifest | None, data)

        manifest = _parse_manifest(d.pop("manifest"))

        name = d.pop("name")

        def _parse_error(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        error = _parse_error(d.pop("error", UNSET))

        agent_info = cls(
            agent_md=agent_md,
            manifest=manifest,
            name=name,
            error=error,
        )

        agent_info.additional_properties = d
        return agent_info

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
