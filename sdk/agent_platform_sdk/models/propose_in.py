from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.def_ref import DefRef


T = TypeVar("T", bound="ProposeIn")


@_attrs_define
class ProposeIn:
    """
    Attributes:
        app (str): App id or name
        request_id (str):
        only (list[DefRef] | None | Unset):
        reason (str | Unset):  Default: ''.
        rollback_to (int | None | Unset):
        transfer_to (None | str | Unset):
    """

    app: str
    request_id: str
    only: list[DefRef] | None | Unset = UNSET
    reason: str | Unset = ""
    rollback_to: int | None | Unset = UNSET
    transfer_to: None | str | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        app = self.app

        request_id = self.request_id

        only: list[dict[str, Any]] | None | Unset
        if isinstance(self.only, Unset):
            only = UNSET
        elif isinstance(self.only, list):
            only = []
            for only_type_0_item_data in self.only:
                only_type_0_item = only_type_0_item_data.to_dict()
                only.append(only_type_0_item)

        else:
            only = self.only

        reason = self.reason

        rollback_to: int | None | Unset
        if isinstance(self.rollback_to, Unset):
            rollback_to = UNSET
        else:
            rollback_to = self.rollback_to

        transfer_to: None | str | Unset
        if isinstance(self.transfer_to, Unset):
            transfer_to = UNSET
        else:
            transfer_to = self.transfer_to

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "app": app,
                "request_id": request_id,
            }
        )
        if only is not UNSET:
            field_dict["only"] = only
        if reason is not UNSET:
            field_dict["reason"] = reason
        if rollback_to is not UNSET:
            field_dict["rollback_to"] = rollback_to
        if transfer_to is not UNSET:
            field_dict["transfer_to"] = transfer_to

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.def_ref import DefRef

        d = dict(src_dict)
        app = d.pop("app")

        request_id = d.pop("request_id")

        def _parse_only(data: object) -> list[DefRef] | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                only_type_0 = []
                _only_type_0 = data
                for only_type_0_item_data in _only_type_0:
                    only_type_0_item = DefRef.from_dict(only_type_0_item_data)

                    only_type_0.append(only_type_0_item)

                return only_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(list[DefRef] | None | Unset, data)

        only = _parse_only(d.pop("only", UNSET))

        reason = d.pop("reason", UNSET)

        def _parse_rollback_to(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        rollback_to = _parse_rollback_to(d.pop("rollback_to", UNSET))

        def _parse_transfer_to(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        transfer_to = _parse_transfer_to(d.pop("transfer_to", UNSET))

        propose_in = cls(
            app=app,
            request_id=request_id,
            only=only,
            reason=reason,
            rollback_to=rollback_to,
            transfer_to=transfer_to,
        )

        return propose_in
