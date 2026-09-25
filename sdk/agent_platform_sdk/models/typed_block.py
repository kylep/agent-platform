from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from typing_extensions import Self

from ..models.typed_block_kind import TypedBlockKind
from ..types import UNSET, Unset

T = TypeVar("T", bound="TypedBlock")


@_attrs_define
class TypedBlock:
    """
    Attributes:
        kind (TypedBlockKind):
        action_alias (None | str | Unset):
        columns (list[str] | Unset):
        field (None | str | Unset):
        href (None | str | Unset):
        label (str | Unset):  Default: ''.
        source (None | str | Unset):
        text (str | Unset):  Default: ''.
        value (str | Unset):  Default: ''.
    """

    kind: TypedBlockKind
    action_alias: None | str | Unset = UNSET
    columns: list[str] | Unset = UNSET
    field: None | str | Unset = UNSET
    href: None | str | Unset = UNSET
    label: str | Unset = ""
    source: None | str | Unset = UNSET
    text: str | Unset = ""
    value: str | Unset = ""

    def to_dict(self) -> dict[str, Any]:
        kind = self.kind.value

        action_alias: None | str | Unset
        if isinstance(self.action_alias, Unset):
            action_alias = UNSET
        else:
            action_alias = self.action_alias

        columns: list[str] | Unset = UNSET
        if not isinstance(self.columns, Unset):
            columns = self.columns

        field: None | str | Unset
        if isinstance(self.field, Unset):
            field = UNSET
        else:
            field = self.field

        href: None | str | Unset
        if isinstance(self.href, Unset):
            href = UNSET
        else:
            href = self.href

        label = self.label

        source: None | str | Unset
        if isinstance(self.source, Unset):
            source = UNSET
        else:
            source = self.source

        text = self.text

        value = self.value

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "kind": kind,
            }
        )
        if action_alias is not UNSET:
            field_dict["action_alias"] = action_alias
        if columns is not UNSET:
            field_dict["columns"] = columns
        if field is not UNSET:
            field_dict["field"] = field
        if href is not UNSET:
            field_dict["href"] = href
        if label is not UNSET:
            field_dict["label"] = label
        if source is not UNSET:
            field_dict["source"] = source
        if text is not UNSET:
            field_dict["text"] = text
        if value is not UNSET:
            field_dict["value"] = value

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        kind = TypedBlockKind(d.pop("kind"))

        def _parse_action_alias(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        action_alias = _parse_action_alias(d.pop("action_alias", UNSET))

        columns = cast(list[str], d.pop("columns", UNSET))

        def _parse_field(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        field = _parse_field(d.pop("field", UNSET))

        def _parse_href(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        href = _parse_href(d.pop("href", UNSET))

        label = d.pop("label", UNSET)

        def _parse_source(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        source = _parse_source(d.pop("source", UNSET))

        text = d.pop("text", UNSET)

        value = d.pop("value", UNSET)

        typed_block = cls(
            kind=kind,
            action_alias=action_alias,
            columns=columns,
            field=field,
            href=href,
            label=label,
            source=source,
            text=text,
            value=value,
        )

        return typed_block
