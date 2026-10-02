from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="NotesIn")


@_attrs_define
class NotesIn:
    """Without `text`, a read; with it, a write under `expected_revision`.

    Attributes:
        app (str): App id or name
        expected_revision (int | None | Unset):
        request_id (None | str | Unset):
        text (None | str | Unset):
    """

    app: str
    expected_revision: int | None | Unset = UNSET
    request_id: None | str | Unset = UNSET
    text: None | str | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        app = self.app

        expected_revision: int | None | Unset
        if isinstance(self.expected_revision, Unset):
            expected_revision = UNSET
        else:
            expected_revision = self.expected_revision

        request_id: None | str | Unset
        if isinstance(self.request_id, Unset):
            request_id = UNSET
        else:
            request_id = self.request_id

        text: None | str | Unset
        if isinstance(self.text, Unset):
            text = UNSET
        else:
            text = self.text

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "app": app,
            }
        )
        if expected_revision is not UNSET:
            field_dict["expected_revision"] = expected_revision
        if request_id is not UNSET:
            field_dict["request_id"] = request_id
        if text is not UNSET:
            field_dict["text"] = text

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        app = d.pop("app")

        def _parse_expected_revision(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        expected_revision = _parse_expected_revision(d.pop("expected_revision", UNSET))

        def _parse_request_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        request_id = _parse_request_id(d.pop("request_id", UNSET))

        def _parse_text(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        text = _parse_text(d.pop("text", UNSET))

        notes_in = cls(
            app=app,
            expected_revision=expected_revision,
            request_id=request_id,
            text=text,
        )

        return notes_in
