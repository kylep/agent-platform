from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.ui_doc_snapshot import UiDocSnapshot
    from ..models.ui_doc_write_out_undo_ops_type_0_item import (
        UiDocWriteOutUndoOpsType0Item,
    )
    from ..models.ui_doc_write_out_warnings_item import UiDocWriteOutWarningsItem


T = TypeVar("T", bound="UiDocWriteOut")


@_attrs_define
class UiDocWriteOut:
    """
    Attributes:
        slug (str):
        version (int):
        folded (bool | Unset):  Default: False.
        ok (bool | Unset):  Default: True.
        replayed (bool | Unset):  Default: False.
        snapshots (list[UiDocSnapshot] | Unset):
        text (str | Unset):  Default: ''.
        undo_ops (list[UiDocWriteOutUndoOpsType0Item] | None | Unset):
        warnings (list[UiDocWriteOutWarningsItem] | Unset):
    """

    slug: str
    version: int
    folded: bool | Unset = False
    ok: bool | Unset = True
    replayed: bool | Unset = False
    snapshots: list[UiDocSnapshot] | Unset = UNSET
    text: str | Unset = ""
    undo_ops: list[UiDocWriteOutUndoOpsType0Item] | None | Unset = UNSET
    warnings: list[UiDocWriteOutWarningsItem] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        slug = self.slug

        version = self.version

        folded = self.folded

        ok = self.ok

        replayed = self.replayed

        snapshots: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.snapshots, Unset):
            snapshots = []
            for snapshots_item_data in self.snapshots:
                snapshots_item = snapshots_item_data.to_dict()
                snapshots.append(snapshots_item)

        text = self.text

        undo_ops: list[dict[str, Any]] | None | Unset
        if isinstance(self.undo_ops, Unset):
            undo_ops = UNSET
        elif isinstance(self.undo_ops, list):
            undo_ops = []
            for undo_ops_type_0_item_data in self.undo_ops:
                undo_ops_type_0_item = undo_ops_type_0_item_data.to_dict()
                undo_ops.append(undo_ops_type_0_item)

        else:
            undo_ops = self.undo_ops

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
                "slug": slug,
                "version": version,
            }
        )
        if folded is not UNSET:
            field_dict["folded"] = folded
        if ok is not UNSET:
            field_dict["ok"] = ok
        if replayed is not UNSET:
            field_dict["replayed"] = replayed
        if snapshots is not UNSET:
            field_dict["snapshots"] = snapshots
        if text is not UNSET:
            field_dict["text"] = text
        if undo_ops is not UNSET:
            field_dict["undo_ops"] = undo_ops
        if warnings is not UNSET:
            field_dict["warnings"] = warnings

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.ui_doc_snapshot import UiDocSnapshot
        from ..models.ui_doc_write_out_undo_ops_type_0_item import (
            UiDocWriteOutUndoOpsType0Item,
        )
        from ..models.ui_doc_write_out_warnings_item import UiDocWriteOutWarningsItem

        d = dict(src_dict)
        slug = d.pop("slug")

        version = d.pop("version")

        folded = d.pop("folded", UNSET)

        ok = d.pop("ok", UNSET)

        replayed = d.pop("replayed", UNSET)

        _snapshots = d.pop("snapshots", UNSET)
        snapshots: list[UiDocSnapshot] | Unset = UNSET
        if _snapshots is not UNSET:
            snapshots = []
            for snapshots_item_data in _snapshots:
                snapshots_item = UiDocSnapshot.from_dict(snapshots_item_data)

                snapshots.append(snapshots_item)

        text = d.pop("text", UNSET)

        def _parse_undo_ops(
            data: object,
        ) -> list[UiDocWriteOutUndoOpsType0Item] | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                undo_ops_type_0 = []
                _undo_ops_type_0 = data
                for undo_ops_type_0_item_data in _undo_ops_type_0:
                    undo_ops_type_0_item = UiDocWriteOutUndoOpsType0Item.from_dict(
                        undo_ops_type_0_item_data
                    )

                    undo_ops_type_0.append(undo_ops_type_0_item)

                return undo_ops_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(list[UiDocWriteOutUndoOpsType0Item] | None | Unset, data)

        undo_ops = _parse_undo_ops(d.pop("undo_ops", UNSET))

        _warnings = d.pop("warnings", UNSET)
        warnings: list[UiDocWriteOutWarningsItem] | Unset = UNSET
        if _warnings is not UNSET:
            warnings = []
            for warnings_item_data in _warnings:
                warnings_item = UiDocWriteOutWarningsItem.from_dict(warnings_item_data)

                warnings.append(warnings_item)

        ui_doc_write_out = cls(
            slug=slug,
            version=version,
            folded=folded,
            ok=ok,
            replayed=replayed,
            snapshots=snapshots,
            text=text,
            undo_ops=undo_ops,
            warnings=warnings,
        )

        ui_doc_write_out.additional_properties = d
        return ui_doc_write_out

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
