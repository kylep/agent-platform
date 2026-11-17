from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.ui_doc_inspect_out_component_set_type_0 import (
        UiDocInspectOutComponentSetType0,
    )
    from ..models.ui_doc_inspect_out_modes import UiDocInspectOutModes
    from ..models.ui_doc_inspect_out_warnings_item import UiDocInspectOutWarningsItem


T = TypeVar("T", bound="UiDocInspectOut")


@_attrs_define
class UiDocInspectOut:
    """
    Attributes:
        id (str):
        slug (str):
        version (int):
        authored (Any | Unset):
        component (None | str | Unset):
        component_set (None | UiDocInspectOutComponentSetType0 | Unset):
        geometry (Any | Unset):
        modes (UiDocInspectOutModes | Unset):
        text (None | str | Unset):
        title (None | str | Unset):
        tokens (list[str] | Unset):
        type_ (None | str | Unset):
        warnings (list[UiDocInspectOutWarningsItem] | Unset):
    """

    id: str
    slug: str
    version: int
    authored: Any | Unset = UNSET
    component: None | str | Unset = UNSET
    component_set: None | UiDocInspectOutComponentSetType0 | Unset = UNSET
    geometry: Any | Unset = UNSET
    modes: UiDocInspectOutModes | Unset = UNSET
    text: None | str | Unset = UNSET
    title: None | str | Unset = UNSET
    tokens: list[str] | Unset = UNSET
    type_: None | str | Unset = UNSET
    warnings: list[UiDocInspectOutWarningsItem] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.ui_doc_inspect_out_component_set_type_0 import (
            UiDocInspectOutComponentSetType0,
        )

        id = self.id

        slug = self.slug

        version = self.version

        authored = self.authored

        component: None | str | Unset
        if isinstance(self.component, Unset):
            component = UNSET
        else:
            component = self.component

        component_set: dict[str, Any] | None | Unset
        if isinstance(self.component_set, Unset):
            component_set = UNSET
        elif isinstance(self.component_set, UiDocInspectOutComponentSetType0):
            component_set = self.component_set.to_dict()
        else:
            component_set = self.component_set

        geometry = self.geometry

        modes: dict[str, Any] | Unset = UNSET
        if not isinstance(self.modes, Unset):
            modes = self.modes.to_dict()

        text: None | str | Unset
        if isinstance(self.text, Unset):
            text = UNSET
        else:
            text = self.text

        title: None | str | Unset
        if isinstance(self.title, Unset):
            title = UNSET
        else:
            title = self.title

        tokens: list[str] | Unset = UNSET
        if not isinstance(self.tokens, Unset):
            tokens = self.tokens

        type_: None | str | Unset
        if isinstance(self.type_, Unset):
            type_ = UNSET
        else:
            type_ = self.type_

        warnings: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.warnings, Unset):
            warnings = []
            for warnings_item_data in self.warnings:
                warnings_item = warnings_item_data.to_dict()
                warnings.append(warnings_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
                "slug": slug,
                "version": version,
            }
        )
        if authored is not UNSET:
            field_dict["authored"] = authored
        if component is not UNSET:
            field_dict["component"] = component
        if component_set is not UNSET:
            field_dict["component_set"] = component_set
        if geometry is not UNSET:
            field_dict["geometry"] = geometry
        if modes is not UNSET:
            field_dict["modes"] = modes
        if text is not UNSET:
            field_dict["text"] = text
        if title is not UNSET:
            field_dict["title"] = title
        if tokens is not UNSET:
            field_dict["tokens"] = tokens
        if type_ is not UNSET:
            field_dict["type"] = type_
        if warnings is not UNSET:
            field_dict["warnings"] = warnings

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.ui_doc_inspect_out_component_set_type_0 import (
            UiDocInspectOutComponentSetType0,
        )
        from ..models.ui_doc_inspect_out_modes import UiDocInspectOutModes
        from ..models.ui_doc_inspect_out_warnings_item import (
            UiDocInspectOutWarningsItem,
        )

        d = dict(src_dict)
        id = d.pop("id")

        slug = d.pop("slug")

        version = d.pop("version")

        authored = d.pop("authored", UNSET)

        def _parse_component(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        component = _parse_component(d.pop("component", UNSET))

        def _parse_component_set(
            data: object,
        ) -> None | UiDocInspectOutComponentSetType0 | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                component_set_type_0 = UiDocInspectOutComponentSetType0.from_dict(data)

                return component_set_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | UiDocInspectOutComponentSetType0 | Unset, data)

        component_set = _parse_component_set(d.pop("component_set", UNSET))

        geometry = d.pop("geometry", UNSET)

        _modes = d.pop("modes", UNSET)
        modes: UiDocInspectOutModes | Unset
        if isinstance(_modes, Unset):
            modes = UNSET
        else:
            modes = UiDocInspectOutModes.from_dict(_modes)

        def _parse_text(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        text = _parse_text(d.pop("text", UNSET))

        def _parse_title(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        title = _parse_title(d.pop("title", UNSET))

        tokens = cast(list[str], d.pop("tokens", UNSET))

        def _parse_type_(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        type_ = _parse_type_(d.pop("type", UNSET))

        _warnings = d.pop("warnings", UNSET)
        warnings: list[UiDocInspectOutWarningsItem] | Unset = UNSET
        if _warnings is not UNSET:
            warnings = []
            for warnings_item_data in _warnings:
                warnings_item = UiDocInspectOutWarningsItem.from_dict(
                    warnings_item_data
                )

                warnings.append(warnings_item)

        ui_doc_inspect_out = cls(
            id=id,
            slug=slug,
            version=version,
            authored=authored,
            component=component,
            component_set=component_set,
            geometry=geometry,
            modes=modes,
            text=text,
            title=title,
            tokens=tokens,
            type_=type_,
            warnings=warnings,
        )

        ui_doc_inspect_out.additional_properties = d
        return ui_doc_inspect_out

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
