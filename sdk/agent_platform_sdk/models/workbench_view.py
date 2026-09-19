from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.workbench_pr import WorkbenchPr


T = TypeVar("T", bound="WorkbenchView")


@_attrs_define
class WorkbenchView:
    """`GET /api/runs/{id}/workbench` (docs/design/24): the facts the runner's
    prepare step is built from. Nothing here is free text.

        Attributes:
            base (str):
            branch (str):
            existing (bool):
            open_pr (None | Unset | WorkbenchPr):
            publish_nonce (None | str | Unset):
            remote_url (None | str | Unset):
            ticket_key (None | str | Unset):
    """

    base: str
    branch: str
    existing: bool
    open_pr: None | Unset | WorkbenchPr = UNSET
    publish_nonce: None | str | Unset = UNSET
    remote_url: None | str | Unset = UNSET
    ticket_key: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.workbench_pr import WorkbenchPr

        base = self.base

        branch = self.branch

        existing = self.existing

        open_pr: dict[str, Any] | None | Unset
        if isinstance(self.open_pr, Unset):
            open_pr = UNSET
        elif isinstance(self.open_pr, WorkbenchPr):
            open_pr = self.open_pr.to_dict()
        else:
            open_pr = self.open_pr

        publish_nonce: None | str | Unset
        if isinstance(self.publish_nonce, Unset):
            publish_nonce = UNSET
        else:
            publish_nonce = self.publish_nonce

        remote_url: None | str | Unset
        if isinstance(self.remote_url, Unset):
            remote_url = UNSET
        else:
            remote_url = self.remote_url

        ticket_key: None | str | Unset
        if isinstance(self.ticket_key, Unset):
            ticket_key = UNSET
        else:
            ticket_key = self.ticket_key

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "base": base,
                "branch": branch,
                "existing": existing,
            }
        )
        if open_pr is not UNSET:
            field_dict["open_pr"] = open_pr
        if publish_nonce is not UNSET:
            field_dict["publish_nonce"] = publish_nonce
        if remote_url is not UNSET:
            field_dict["remote_url"] = remote_url
        if ticket_key is not UNSET:
            field_dict["ticket_key"] = ticket_key

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.workbench_pr import WorkbenchPr

        d = dict(src_dict)
        base = d.pop("base")

        branch = d.pop("branch")

        existing = d.pop("existing")

        def _parse_open_pr(data: object) -> None | Unset | WorkbenchPr:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                open_pr_type_0 = WorkbenchPr.from_dict(data)

                return open_pr_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | Unset | WorkbenchPr, data)

        open_pr = _parse_open_pr(d.pop("open_pr", UNSET))

        def _parse_publish_nonce(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        publish_nonce = _parse_publish_nonce(d.pop("publish_nonce", UNSET))

        def _parse_remote_url(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        remote_url = _parse_remote_url(d.pop("remote_url", UNSET))

        def _parse_ticket_key(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        ticket_key = _parse_ticket_key(d.pop("ticket_key", UNSET))

        workbench_view = cls(
            base=base,
            branch=branch,
            existing=existing,
            open_pr=open_pr,
            publish_nonce=publish_nonce,
            remote_url=remote_url,
            ticket_key=ticket_key,
        )

        workbench_view.additional_properties = d
        return workbench_view

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
