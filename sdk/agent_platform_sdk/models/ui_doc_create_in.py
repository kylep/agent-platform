from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.ui_doc_create_in_doc_type_0 import UiDocCreateInDocType0


T = TypeVar("T", bound="UiDocCreateIn")


@_attrs_define
class UiDocCreateIn:
    """
    Attributes:
        slug (str):
        description (str | Unset):  Default: ''.
        doc (None | UiDocCreateInDocType0 | Unset):
        reason (str | Unset):  Default: ''.
        request_id (None | str | Unset):
        template_id (None | str | Unset):
        template_revision (int | None | Unset):
        title (str | Unset):  Default: ''.
    """

    slug: str
    description: str | Unset = ""
    doc: None | UiDocCreateInDocType0 | Unset = UNSET
    reason: str | Unset = ""
    request_id: None | str | Unset = UNSET
    template_id: None | str | Unset = UNSET
    template_revision: int | None | Unset = UNSET
    title: str | Unset = ""
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.ui_doc_create_in_doc_type_0 import UiDocCreateInDocType0

        slug = self.slug

        description = self.description

        doc: dict[str, Any] | None | Unset
        if isinstance(self.doc, Unset):
            doc = UNSET
        elif isinstance(self.doc, UiDocCreateInDocType0):
            doc = self.doc.to_dict()
        else:
            doc = self.doc

        reason = self.reason

        request_id: None | str | Unset
        if isinstance(self.request_id, Unset):
            request_id = UNSET
        else:
            request_id = self.request_id

        template_id: None | str | Unset
        if isinstance(self.template_id, Unset):
            template_id = UNSET
        else:
            template_id = self.template_id

        template_revision: int | None | Unset
        if isinstance(self.template_revision, Unset):
            template_revision = UNSET
        else:
            template_revision = self.template_revision

        title = self.title

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "slug": slug,
            }
        )
        if description is not UNSET:
            field_dict["description"] = description
        if doc is not UNSET:
            field_dict["doc"] = doc
        if reason is not UNSET:
            field_dict["reason"] = reason
        if request_id is not UNSET:
            field_dict["request_id"] = request_id
        if template_id is not UNSET:
            field_dict["template_id"] = template_id
        if template_revision is not UNSET:
            field_dict["template_revision"] = template_revision
        if title is not UNSET:
            field_dict["title"] = title

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.ui_doc_create_in_doc_type_0 import UiDocCreateInDocType0

        d = dict(src_dict)
        slug = d.pop("slug")

        description = d.pop("description", UNSET)

        def _parse_doc(data: object) -> None | UiDocCreateInDocType0 | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                doc_type_0 = UiDocCreateInDocType0.from_dict(data)

                return doc_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | UiDocCreateInDocType0 | Unset, data)

        doc = _parse_doc(d.pop("doc", UNSET))

        reason = d.pop("reason", UNSET)

        def _parse_request_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        request_id = _parse_request_id(d.pop("request_id", UNSET))

        def _parse_template_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        template_id = _parse_template_id(d.pop("template_id", UNSET))

        def _parse_template_revision(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        template_revision = _parse_template_revision(d.pop("template_revision", UNSET))

        title = d.pop("title", UNSET)

        ui_doc_create_in = cls(
            slug=slug,
            description=description,
            doc=doc,
            reason=reason,
            request_id=request_id,
            template_id=template_id,
            template_revision=template_revision,
            title=title,
        )

        ui_doc_create_in.additional_properties = d
        return ui_doc_create_in

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
