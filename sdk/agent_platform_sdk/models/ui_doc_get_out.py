from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.ui_doc_get_out_cursor_type_0 import UiDocGetOutCursorType0


T = TypeVar("T", bound="UiDocGetOut")


@_attrs_define
class UiDocGetOut:
    """
    Attributes:
        at_version (int):
        bytes_ (int):
        doc (Any):
        doc_sha256 (str):
        expanded_count (int):
        id (str):
        node_count (int):
        owner (str):
        slug (str):
        title (str):
        version (int):
        created_at (None | str | Unset):
        cursor (None | UiDocGetOutCursorType0 | Unset):
        deleted_at (None | str | Unset):
        description (str | Unset):  Default: ''.
        node (None | str | Unset):
        notes (list[str] | Unset):
        resolved (Any | Unset):
        state (None | str | Unset):
        truncated (bool | Unset):  Default: False.
        updated_at (None | str | Unset):
    """

    at_version: int
    bytes_: int
    doc: Any
    doc_sha256: str
    expanded_count: int
    id: str
    node_count: int
    owner: str
    slug: str
    title: str
    version: int
    created_at: None | str | Unset = UNSET
    cursor: None | UiDocGetOutCursorType0 | Unset = UNSET
    deleted_at: None | str | Unset = UNSET
    description: str | Unset = ""
    node: None | str | Unset = UNSET
    notes: list[str] | Unset = UNSET
    resolved: Any | Unset = UNSET
    state: None | str | Unset = UNSET
    truncated: bool | Unset = False
    updated_at: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.ui_doc_get_out_cursor_type_0 import UiDocGetOutCursorType0

        at_version = self.at_version

        bytes_ = self.bytes_

        doc = self.doc

        doc_sha256 = self.doc_sha256

        expanded_count = self.expanded_count

        id = self.id

        node_count = self.node_count

        owner = self.owner

        slug = self.slug

        title = self.title

        version = self.version

        created_at: None | str | Unset
        if isinstance(self.created_at, Unset):
            created_at = UNSET
        else:
            created_at = self.created_at

        cursor: dict[str, Any] | None | Unset
        if isinstance(self.cursor, Unset):
            cursor = UNSET
        elif isinstance(self.cursor, UiDocGetOutCursorType0):
            cursor = self.cursor.to_dict()
        else:
            cursor = self.cursor

        deleted_at: None | str | Unset
        if isinstance(self.deleted_at, Unset):
            deleted_at = UNSET
        else:
            deleted_at = self.deleted_at

        description = self.description

        node: None | str | Unset
        if isinstance(self.node, Unset):
            node = UNSET
        else:
            node = self.node

        notes: list[str] | Unset = UNSET
        if not isinstance(self.notes, Unset):
            notes = self.notes

        resolved = self.resolved

        state: None | str | Unset
        if isinstance(self.state, Unset):
            state = UNSET
        else:
            state = self.state

        truncated = self.truncated

        updated_at: None | str | Unset
        if isinstance(self.updated_at, Unset):
            updated_at = UNSET
        else:
            updated_at = self.updated_at

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "at_version": at_version,
                "bytes": bytes_,
                "doc": doc,
                "doc_sha256": doc_sha256,
                "expanded_count": expanded_count,
                "id": id,
                "node_count": node_count,
                "owner": owner,
                "slug": slug,
                "title": title,
                "version": version,
            }
        )
        if created_at is not UNSET:
            field_dict["created_at"] = created_at
        if cursor is not UNSET:
            field_dict["cursor"] = cursor
        if deleted_at is not UNSET:
            field_dict["deleted_at"] = deleted_at
        if description is not UNSET:
            field_dict["description"] = description
        if node is not UNSET:
            field_dict["node"] = node
        if notes is not UNSET:
            field_dict["notes"] = notes
        if resolved is not UNSET:
            field_dict["resolved"] = resolved
        if state is not UNSET:
            field_dict["state"] = state
        if truncated is not UNSET:
            field_dict["truncated"] = truncated
        if updated_at is not UNSET:
            field_dict["updated_at"] = updated_at

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.ui_doc_get_out_cursor_type_0 import UiDocGetOutCursorType0

        d = dict(src_dict)
        at_version = d.pop("at_version")

        bytes_ = d.pop("bytes")

        doc = d.pop("doc")

        doc_sha256 = d.pop("doc_sha256")

        expanded_count = d.pop("expanded_count")

        id = d.pop("id")

        node_count = d.pop("node_count")

        owner = d.pop("owner")

        slug = d.pop("slug")

        title = d.pop("title")

        version = d.pop("version")

        def _parse_created_at(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        created_at = _parse_created_at(d.pop("created_at", UNSET))

        def _parse_cursor(data: object) -> None | UiDocGetOutCursorType0 | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                cursor_type_0 = UiDocGetOutCursorType0.from_dict(data)

                return cursor_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | UiDocGetOutCursorType0 | Unset, data)

        cursor = _parse_cursor(d.pop("cursor", UNSET))

        def _parse_deleted_at(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        deleted_at = _parse_deleted_at(d.pop("deleted_at", UNSET))

        description = d.pop("description", UNSET)

        def _parse_node(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        node = _parse_node(d.pop("node", UNSET))

        notes = cast(list[str], d.pop("notes", UNSET))

        resolved = d.pop("resolved", UNSET)

        def _parse_state(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        state = _parse_state(d.pop("state", UNSET))

        truncated = d.pop("truncated", UNSET)

        def _parse_updated_at(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        updated_at = _parse_updated_at(d.pop("updated_at", UNSET))

        ui_doc_get_out = cls(
            at_version=at_version,
            bytes_=bytes_,
            doc=doc,
            doc_sha256=doc_sha256,
            expanded_count=expanded_count,
            id=id,
            node_count=node_count,
            owner=owner,
            slug=slug,
            title=title,
            version=version,
            created_at=created_at,
            cursor=cursor,
            deleted_at=deleted_at,
            description=description,
            node=node,
            notes=notes,
            resolved=resolved,
            state=state,
            truncated=truncated,
            updated_at=updated_at,
        )

        ui_doc_get_out.additional_properties = d
        return ui_doc_get_out

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
