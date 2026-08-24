from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.entrypoints_model import EntrypointsModel
    from ..models.manifest import Manifest


T = TypeVar("T", bound="AgentInfo")


@_attrs_define
class AgentInfo:
    """One agent as the platform reads it. Also the `GET /api/agents/{name}`
    response model (re-exported by api.schemas).

        Attributes:
            agent_md (str):
            manifest (Manifest | None):
            name (str):
            enabled (bool | Unset):  Default: True.
            entrypoints (EntrypointsModel | Unset): The agent's defining triggers (formerly entrypoints.yaml): cron fires,
                inbound webhook paths (POST /api/webhooks/<path> only works for a declared
                path), and kafka topic subscriptions. Distinct from DB Jobs, which are
                ad-hoc UI experiments — history, not config.
            error (None | str | Unset):
            harness_tools (list[str] | Unset):
            platform_tools (list[str] | Unset):
    """

    agent_md: str
    manifest: Manifest | None
    name: str
    enabled: bool | Unset = True
    entrypoints: EntrypointsModel | Unset = UNSET
    error: None | str | Unset = UNSET
    harness_tools: list[str] | Unset = UNSET
    platform_tools: list[str] | Unset = UNSET
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

        enabled = self.enabled

        entrypoints: dict[str, Any] | Unset = UNSET
        if not isinstance(self.entrypoints, Unset):
            entrypoints = self.entrypoints.to_dict()

        error: None | str | Unset
        if isinstance(self.error, Unset):
            error = UNSET
        else:
            error = self.error

        harness_tools: list[str] | Unset = UNSET
        if not isinstance(self.harness_tools, Unset):
            harness_tools = self.harness_tools

        platform_tools: list[str] | Unset = UNSET
        if not isinstance(self.platform_tools, Unset):
            platform_tools = self.platform_tools

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "agent_md": agent_md,
                "manifest": manifest,
                "name": name,
            }
        )
        if enabled is not UNSET:
            field_dict["enabled"] = enabled
        if entrypoints is not UNSET:
            field_dict["entrypoints"] = entrypoints
        if error is not UNSET:
            field_dict["error"] = error
        if harness_tools is not UNSET:
            field_dict["harness_tools"] = harness_tools
        if platform_tools is not UNSET:
            field_dict["platform_tools"] = platform_tools

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.entrypoints_model import EntrypointsModel
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

        enabled = d.pop("enabled", UNSET)

        _entrypoints = d.pop("entrypoints", UNSET)
        entrypoints: EntrypointsModel | Unset
        if isinstance(_entrypoints, Unset):
            entrypoints = UNSET
        else:
            entrypoints = EntrypointsModel.from_dict(_entrypoints)

        def _parse_error(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        error = _parse_error(d.pop("error", UNSET))

        harness_tools = cast(list[str], d.pop("harness_tools", UNSET))

        platform_tools = cast(list[str], d.pop("platform_tools", UNSET))

        agent_info = cls(
            agent_md=agent_md,
            manifest=manifest,
            name=name,
            enabled=enabled,
            entrypoints=entrypoints,
            error=error,
            harness_tools=harness_tools,
            platform_tools=platform_tools,
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
