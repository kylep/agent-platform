from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

T = TypeVar("T", bound="PageActionDispatchIn")


@_attrs_define
class PageActionDispatchIn:
    """
    Attributes:
        digest (str):
    """

    digest: str

    def to_dict(self) -> dict[str, Any]:
        digest = self.digest

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "digest": digest,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        digest = d.pop("digest")

        page_action_dispatch_in = cls(
            digest=digest,
        )

        return page_action_dispatch_in
