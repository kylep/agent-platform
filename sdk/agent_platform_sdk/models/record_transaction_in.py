from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.record_transaction_in_guards_item import RecordTransactionInGuardsItem
    from ..models.record_transaction_in_operations_item import (
        RecordTransactionInOperationsItem,
    )


T = TypeVar("T", bound="RecordTransactionIn")


@_attrs_define
class RecordTransactionIn:
    """
    Attributes:
        app (str): App id or name
        operations (list[RecordTransactionInOperationsItem]):
        request_id (str):
        guards (list[RecordTransactionInGuardsItem] | Unset):
    """

    app: str
    operations: list[RecordTransactionInOperationsItem]
    request_id: str
    guards: list[RecordTransactionInGuardsItem] | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        app = self.app

        operations = []
        for operations_item_data in self.operations:
            operations_item = operations_item_data.to_dict()
            operations.append(operations_item)

        request_id = self.request_id

        guards: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.guards, Unset):
            guards = []
            for guards_item_data in self.guards:
                guards_item = guards_item_data.to_dict()
                guards.append(guards_item)

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "app": app,
                "operations": operations,
                "request_id": request_id,
            }
        )
        if guards is not UNSET:
            field_dict["guards"] = guards

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.record_transaction_in_guards_item import (
            RecordTransactionInGuardsItem,
        )
        from ..models.record_transaction_in_operations_item import (
            RecordTransactionInOperationsItem,
        )

        d = dict(src_dict)
        app = d.pop("app")

        operations = []
        _operations = d.pop("operations")
        for operations_item_data in _operations:
            operations_item = RecordTransactionInOperationsItem.from_dict(
                operations_item_data
            )

            operations.append(operations_item)

        request_id = d.pop("request_id")

        _guards = d.pop("guards", UNSET)
        guards: list[RecordTransactionInGuardsItem] | Unset = UNSET
        if _guards is not UNSET:
            guards = []
            for guards_item_data in _guards:
                guards_item = RecordTransactionInGuardsItem.from_dict(guards_item_data)

                guards.append(guards_item)

        record_transaction_in = cls(
            app=app,
            operations=operations,
            request_id=request_id,
            guards=guards,
        )

        return record_transaction_in
