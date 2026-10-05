from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.staging_write_in_mode import StagingWriteInMode
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.staging_write_in_records_item import StagingWriteInRecordsItem


T = TypeVar("T", bound="StagingWriteIn")


@_attrs_define
class StagingWriteIn:
    """
    Attributes:
        app (str): App id or name
        collection (str):
        records (list[StagingWriteInRecordsItem]):
        set_id (str):
        key (list[str] | None | Unset):
        mode (StagingWriteInMode | Unset):  Default: StagingWriteInMode.INSERT.
    """

    app: str
    collection: str
    records: list[StagingWriteInRecordsItem]
    set_id: str
    key: list[str] | None | Unset = UNSET
    mode: StagingWriteInMode | Unset = StagingWriteInMode.INSERT

    def to_dict(self) -> dict[str, Any]:
        app = self.app

        collection = self.collection

        records = []
        for records_item_data in self.records:
            records_item = records_item_data.to_dict()
            records.append(records_item)

        set_id = self.set_id

        key: list[str] | None | Unset
        if isinstance(self.key, Unset):
            key = UNSET
        elif isinstance(self.key, list):
            key = self.key

        else:
            key = self.key

        mode: str | Unset = UNSET
        if not isinstance(self.mode, Unset):
            mode = self.mode.value

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "app": app,
                "collection": collection,
                "records": records,
                "set_id": set_id,
            }
        )
        if key is not UNSET:
            field_dict["key"] = key
        if mode is not UNSET:
            field_dict["mode"] = mode

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.staging_write_in_records_item import StagingWriteInRecordsItem

        d = dict(src_dict)
        app = d.pop("app")

        collection = d.pop("collection")

        records = []
        _records = d.pop("records")
        for records_item_data in _records:
            records_item = StagingWriteInRecordsItem.from_dict(records_item_data)

            records.append(records_item)

        set_id = d.pop("set_id")

        def _parse_key(data: object) -> list[str] | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                key_type_0 = cast(list[str], data)

                return key_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(list[str] | None | Unset, data)

        key = _parse_key(d.pop("key", UNSET))

        _mode = d.pop("mode", UNSET)
        mode: StagingWriteInMode | Unset
        if isinstance(_mode, Unset):
            mode = UNSET
        else:
            mode = StagingWriteInMode(_mode)

        staging_write_in = cls(
            app=app,
            collection=collection,
            records=records,
            set_id=set_id,
            key=key,
            mode=mode,
        )

        return staging_write_in
