from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.def_ref import DefRef


T = TypeVar("T", bound="ValidateIn")


@_attrs_define
class ValidateIn:
    """
    Attributes:
        app (str): App id or name
        expected_approved_version (int | None | Unset):
        only (list[DefRef] | None | Unset):
    """

    app: str
    expected_approved_version: int | None | Unset = UNSET
    only: list[DefRef] | None | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        app = self.app

        expected_approved_version: int | None | Unset
        if isinstance(self.expected_approved_version, Unset):
            expected_approved_version = UNSET
        else:
            expected_approved_version = self.expected_approved_version

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

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "app": app,
            }
        )
        if expected_approved_version is not UNSET:
            field_dict["expected_approved_version"] = expected_approved_version
        if only is not UNSET:
            field_dict["only"] = only

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.def_ref import DefRef

        d = dict(src_dict)
        app = d.pop("app")

        def _parse_expected_approved_version(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        expected_approved_version = _parse_expected_approved_version(
            d.pop("expected_approved_version", UNSET)
        )

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

        validate_in = cls(
            app=app,
            expected_approved_version=expected_approved_version,
            only=only,
        )

        return validate_in
