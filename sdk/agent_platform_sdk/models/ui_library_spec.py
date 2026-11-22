from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="UiLibrarySpec")


@_attrs_define
class UiLibrarySpec:
    """
    Attributes:
        namespace (str):
        source_id (str):
        source_version (int):
        component_sets (list[str] | Unset):
        components (list[str] | Unset):
        tokens (list[str] | Unset):
    """

    namespace: str
    source_id: str
    source_version: int
    component_sets: list[str] | Unset = UNSET
    components: list[str] | Unset = UNSET
    tokens: list[str] | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        namespace = self.namespace

        source_id = self.source_id

        source_version = self.source_version

        component_sets: list[str] | Unset = UNSET
        if not isinstance(self.component_sets, Unset):
            component_sets = self.component_sets

        components: list[str] | Unset = UNSET
        if not isinstance(self.components, Unset):
            components = self.components

        tokens: list[str] | Unset = UNSET
        if not isinstance(self.tokens, Unset):
            tokens = self.tokens

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "namespace": namespace,
                "source_id": source_id,
                "source_version": source_version,
            }
        )
        if component_sets is not UNSET:
            field_dict["component_sets"] = component_sets
        if components is not UNSET:
            field_dict["components"] = components
        if tokens is not UNSET:
            field_dict["tokens"] = tokens

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        namespace = d.pop("namespace")

        source_id = d.pop("source_id")

        source_version = d.pop("source_version")

        component_sets = cast(list[str], d.pop("component_sets", UNSET))

        components = cast(list[str], d.pop("components", UNSET))

        tokens = cast(list[str], d.pop("tokens", UNSET))

        ui_library_spec = cls(
            namespace=namespace,
            source_id=source_id,
            source_version=source_version,
            component_sets=component_sets,
            components=components,
            tokens=tokens,
        )

        return ui_library_spec
