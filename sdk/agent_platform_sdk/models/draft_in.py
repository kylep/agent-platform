from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.draft_in_definition_type_0 import DraftInDefinitionType0


T = TypeVar("T", bound="DraftIn")


@_attrs_define
class DraftIn:
    """
    Attributes:
        app (str): App id or name
        kind (str):
        request_id (str):
        definition (DraftInDefinitionType0 | None | Unset):
        discard (bool | Unset):  Default: False.
        expected_revision (int | None | Unset):
        name (None | str | Unset):
        reason (str | Unset):  Default: ''.
        remove (bool | Unset):  Default: False.
    """

    app: str
    kind: str
    request_id: str
    definition: DraftInDefinitionType0 | None | Unset = UNSET
    discard: bool | Unset = False
    expected_revision: int | None | Unset = UNSET
    name: None | str | Unset = UNSET
    reason: str | Unset = ""
    remove: bool | Unset = False

    def to_dict(self) -> dict[str, Any]:
        from ..models.draft_in_definition_type_0 import DraftInDefinitionType0

        app = self.app

        kind = self.kind

        request_id = self.request_id

        definition: dict[str, Any] | None | Unset
        if isinstance(self.definition, Unset):
            definition = UNSET
        elif isinstance(self.definition, DraftInDefinitionType0):
            definition = self.definition.to_dict()
        else:
            definition = self.definition

        discard = self.discard

        expected_revision: int | None | Unset
        if isinstance(self.expected_revision, Unset):
            expected_revision = UNSET
        else:
            expected_revision = self.expected_revision

        name: None | str | Unset
        if isinstance(self.name, Unset):
            name = UNSET
        else:
            name = self.name

        reason = self.reason

        remove = self.remove

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "app": app,
                "kind": kind,
                "request_id": request_id,
            }
        )
        if definition is not UNSET:
            field_dict["definition"] = definition
        if discard is not UNSET:
            field_dict["discard"] = discard
        if expected_revision is not UNSET:
            field_dict["expected_revision"] = expected_revision
        if name is not UNSET:
            field_dict["name"] = name
        if reason is not UNSET:
            field_dict["reason"] = reason
        if remove is not UNSET:
            field_dict["remove"] = remove

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.draft_in_definition_type_0 import DraftInDefinitionType0

        d = dict(src_dict)
        app = d.pop("app")

        kind = d.pop("kind")

        request_id = d.pop("request_id")

        def _parse_definition(data: object) -> DraftInDefinitionType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                definition_type_0 = DraftInDefinitionType0.from_dict(data)

                return definition_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(DraftInDefinitionType0 | None | Unset, data)

        definition = _parse_definition(d.pop("definition", UNSET))

        discard = d.pop("discard", UNSET)

        def _parse_expected_revision(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        expected_revision = _parse_expected_revision(d.pop("expected_revision", UNSET))

        def _parse_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        name = _parse_name(d.pop("name", UNSET))

        reason = d.pop("reason", UNSET)

        remove = d.pop("remove", UNSET)

        draft_in = cls(
            app=app,
            kind=kind,
            request_id=request_id,
            definition=definition,
            discard=discard,
            expected_revision=expected_revision,
            name=name,
            reason=reason,
            remove=remove,
        )

        return draft_in
