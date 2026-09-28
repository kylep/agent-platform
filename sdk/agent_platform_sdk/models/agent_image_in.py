from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

T = TypeVar("T", bound="AgentImageIn")


@_attrs_define
class AgentImageIn:
    """`PUT /api/agents/{name}/image` (docs/design/23): the picture's artifact,
    or null to take it off. The key is required so an empty body is a 422 and
    not a silent clear.

        Attributes:
            artifact_id (None | str):
    """

    artifact_id: None | str

    def to_dict(self) -> dict[str, Any]:
        artifact_id: None | str
        artifact_id = self.artifact_id

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "artifact_id": artifact_id,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)

        def _parse_artifact_id(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        artifact_id = _parse_artifact_id(d.pop("artifact_id"))

        agent_image_in = cls(
            artifact_id=artifact_id,
        )

        return agent_image_in
