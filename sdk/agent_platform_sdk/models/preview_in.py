from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.preview_in_params import PreviewInParams
    from ..models.preview_in_samples_type_0 import PreviewInSamplesType0


T = TypeVar("T", bound="PreviewIn")


@_attrs_define
class PreviewIn:
    """
    Attributes:
        app (str): App id or name
        kind (str):
        name (str):
        as_ (None | str | Unset):
        cursor (None | str | Unset):
        limit (int | None | Unset):
        params (PreviewInParams | Unset):
        samples (None | PreviewInSamplesType0 | Unset):
    """

    app: str
    kind: str
    name: str
    as_: None | str | Unset = UNSET
    cursor: None | str | Unset = UNSET
    limit: int | None | Unset = UNSET
    params: PreviewInParams | Unset = UNSET
    samples: None | PreviewInSamplesType0 | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.preview_in_samples_type_0 import PreviewInSamplesType0

        app = self.app

        kind = self.kind

        name = self.name

        as_: None | str | Unset
        if isinstance(self.as_, Unset):
            as_ = UNSET
        else:
            as_ = self.as_

        cursor: None | str | Unset
        if isinstance(self.cursor, Unset):
            cursor = UNSET
        else:
            cursor = self.cursor

        limit: int | None | Unset
        if isinstance(self.limit, Unset):
            limit = UNSET
        else:
            limit = self.limit

        params: dict[str, Any] | Unset = UNSET
        if not isinstance(self.params, Unset):
            params = self.params.to_dict()

        samples: dict[str, Any] | None | Unset
        if isinstance(self.samples, Unset):
            samples = UNSET
        elif isinstance(self.samples, PreviewInSamplesType0):
            samples = self.samples.to_dict()
        else:
            samples = self.samples

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "app": app,
                "kind": kind,
                "name": name,
            }
        )
        if as_ is not UNSET:
            field_dict["as"] = as_
        if cursor is not UNSET:
            field_dict["cursor"] = cursor
        if limit is not UNSET:
            field_dict["limit"] = limit
        if params is not UNSET:
            field_dict["params"] = params
        if samples is not UNSET:
            field_dict["samples"] = samples

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.preview_in_params import PreviewInParams
        from ..models.preview_in_samples_type_0 import PreviewInSamplesType0

        d = dict(src_dict)
        app = d.pop("app")

        kind = d.pop("kind")

        name = d.pop("name")

        def _parse_as_(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        as_ = _parse_as_(d.pop("as", UNSET))

        def _parse_cursor(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        cursor = _parse_cursor(d.pop("cursor", UNSET))

        def _parse_limit(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        limit = _parse_limit(d.pop("limit", UNSET))

        _params = d.pop("params", UNSET)
        params: PreviewInParams | Unset
        if isinstance(_params, Unset):
            params = UNSET
        else:
            params = PreviewInParams.from_dict(_params)

        def _parse_samples(data: object) -> None | PreviewInSamplesType0 | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                samples_type_0 = PreviewInSamplesType0.from_dict(data)

                return samples_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | PreviewInSamplesType0 | Unset, data)

        samples = _parse_samples(d.pop("samples", UNSET))

        preview_in = cls(
            app=app,
            kind=kind,
            name=name,
            as_=as_,
            cursor=cursor,
            limit=limit,
            params=params,
            samples=samples,
        )

        return preview_in
