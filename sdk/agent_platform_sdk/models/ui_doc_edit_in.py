from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.ui_doc_edit_in_ops_item import UiDocEditInOpsItem


T = TypeVar("T", bound="UiDocEditIn")


@_attrs_define
class UiDocEditIn:
    """
    Attributes:
        expected_version (int):
        ops (list[UiDocEditInOpsItem]):
        annotate (bool | Unset):  Default: False.
        mode (None | str | Unset):
        reason (str | Unset):  Default: ''.
        request_id (None | str | Unset):
        scale (float | Unset):  Default: 1.0.
        snapshot (bool | list[None | str] | None | Unset):
    """

    expected_version: int
    ops: list[UiDocEditInOpsItem]
    annotate: bool | Unset = False
    mode: None | str | Unset = UNSET
    reason: str | Unset = ""
    request_id: None | str | Unset = UNSET
    scale: float | Unset = 1.0
    snapshot: bool | list[None | str] | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        expected_version = self.expected_version

        ops = []
        for ops_item_data in self.ops:
            ops_item = ops_item_data.to_dict()
            ops.append(ops_item)

        annotate = self.annotate

        mode: None | str | Unset
        if isinstance(self.mode, Unset):
            mode = UNSET
        else:
            mode = self.mode

        reason = self.reason

        request_id: None | str | Unset
        if isinstance(self.request_id, Unset):
            request_id = UNSET
        else:
            request_id = self.request_id

        scale = self.scale

        snapshot: bool | list[None | str] | None | Unset
        if isinstance(self.snapshot, Unset):
            snapshot = UNSET
        elif isinstance(self.snapshot, list):
            snapshot = []
            for snapshot_type_0_item_data in self.snapshot:
                snapshot_type_0_item: None | str
                snapshot_type_0_item = snapshot_type_0_item_data
                snapshot.append(snapshot_type_0_item)

        else:
            snapshot = self.snapshot

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "expected_version": expected_version,
                "ops": ops,
            }
        )
        if annotate is not UNSET:
            field_dict["annotate"] = annotate
        if mode is not UNSET:
            field_dict["mode"] = mode
        if reason is not UNSET:
            field_dict["reason"] = reason
        if request_id is not UNSET:
            field_dict["request_id"] = request_id
        if scale is not UNSET:
            field_dict["scale"] = scale
        if snapshot is not UNSET:
            field_dict["snapshot"] = snapshot

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.ui_doc_edit_in_ops_item import UiDocEditInOpsItem

        d = dict(src_dict)
        expected_version = d.pop("expected_version")

        ops = []
        _ops = d.pop("ops")
        for ops_item_data in _ops:
            ops_item = UiDocEditInOpsItem.from_dict(ops_item_data)

            ops.append(ops_item)

        annotate = d.pop("annotate", UNSET)

        def _parse_mode(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        mode = _parse_mode(d.pop("mode", UNSET))

        reason = d.pop("reason", UNSET)

        def _parse_request_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        request_id = _parse_request_id(d.pop("request_id", UNSET))

        scale = d.pop("scale", UNSET)

        def _parse_snapshot(data: object) -> bool | list[None | str] | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                snapshot_type_0 = []
                _snapshot_type_0 = data
                for snapshot_type_0_item_data in _snapshot_type_0:

                    def _parse_snapshot_type_0_item(data: object) -> None | str:
                        if data is None:
                            return data
                        return cast(None | str, data)

                    snapshot_type_0_item = _parse_snapshot_type_0_item(
                        snapshot_type_0_item_data
                    )

                    snapshot_type_0.append(snapshot_type_0_item)

                return snapshot_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(bool | list[None | str] | None | Unset, data)

        snapshot = _parse_snapshot(d.pop("snapshot", UNSET))

        ui_doc_edit_in = cls(
            expected_version=expected_version,
            ops=ops,
            annotate=annotate,
            mode=mode,
            reason=reason,
            request_id=request_id,
            scale=scale,
            snapshot=snapshot,
        )

        ui_doc_edit_in.additional_properties = d
        return ui_doc_edit_in

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
