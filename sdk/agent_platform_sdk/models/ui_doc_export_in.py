from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.ui_doc_export_in_format import UiDocExportInFormat
from ..types import UNSET, Unset

T = TypeVar("T", bound="UiDocExportIn")


@_attrs_define
class UiDocExportIn:
    """
    Attributes:
        format_ (UiDocExportInFormat | Unset):  Default: UiDocExportInFormat.PNG.
        mode (None | str | Unset):
        name (None | str | Unset):
        node (None | str | Unset):
        scale (float | Unset):  Default: 1.0.
        version (int | None | Unset):
    """

    format_: UiDocExportInFormat | Unset = UiDocExportInFormat.PNG
    mode: None | str | Unset = UNSET
    name: None | str | Unset = UNSET
    node: None | str | Unset = UNSET
    scale: float | Unset = 1.0
    version: int | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        format_: str | Unset = UNSET
        if not isinstance(self.format_, Unset):
            format_ = self.format_.value

        mode: None | str | Unset
        if isinstance(self.mode, Unset):
            mode = UNSET
        else:
            mode = self.mode

        name: None | str | Unset
        if isinstance(self.name, Unset):
            name = UNSET
        else:
            name = self.name

        node: None | str | Unset
        if isinstance(self.node, Unset):
            node = UNSET
        else:
            node = self.node

        scale = self.scale

        version: int | None | Unset
        if isinstance(self.version, Unset):
            version = UNSET
        else:
            version = self.version

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if format_ is not UNSET:
            field_dict["format"] = format_
        if mode is not UNSET:
            field_dict["mode"] = mode
        if name is not UNSET:
            field_dict["name"] = name
        if node is not UNSET:
            field_dict["node"] = node
        if scale is not UNSET:
            field_dict["scale"] = scale
        if version is not UNSET:
            field_dict["version"] = version

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        _format_ = d.pop("format", UNSET)
        format_: UiDocExportInFormat | Unset
        if isinstance(_format_, Unset):
            format_ = UNSET
        else:
            format_ = UiDocExportInFormat(_format_)

        def _parse_mode(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        mode = _parse_mode(d.pop("mode", UNSET))

        def _parse_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        name = _parse_name(d.pop("name", UNSET))

        def _parse_node(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        node = _parse_node(d.pop("node", UNSET))

        scale = d.pop("scale", UNSET)

        def _parse_version(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        version = _parse_version(d.pop("version", UNSET))

        ui_doc_export_in = cls(
            format_=format_,
            mode=mode,
            name=name,
            node=node,
            scale=scale,
            version=version,
        )

        ui_doc_export_in.additional_properties = d
        return ui_doc_export_in

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
