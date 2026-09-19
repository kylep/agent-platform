from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.publish_run_publish_in_verify_type_0 import (
        PublishRunPublishInVerifyType0,
    )


T = TypeVar("T", bound="PublishRunPublishIn")


@_attrs_define
class PublishRunPublishIn:
    """What `services/runner/workbench.py::finalize` POSTs. `verify` and
    `notes_md` are untrusted: the route hands them to the publish service,
    which shape-checks the one and strips the other.

        Attributes:
            base_sha (str):
            bundle_b64 (str):
            head_sha (str):
            notes_md (str | Unset):  Default: ''.
            verify (None | PublishRunPublishInVerifyType0 | Unset):
    """

    base_sha: str
    bundle_b64: str
    head_sha: str
    notes_md: str | Unset = ""
    verify: None | PublishRunPublishInVerifyType0 | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.publish_run_publish_in_verify_type_0 import (
            PublishRunPublishInVerifyType0,
        )

        base_sha = self.base_sha

        bundle_b64 = self.bundle_b64

        head_sha = self.head_sha

        notes_md = self.notes_md

        verify: dict[str, Any] | None | Unset
        if isinstance(self.verify, Unset):
            verify = UNSET
        elif isinstance(self.verify, PublishRunPublishInVerifyType0):
            verify = self.verify.to_dict()
        else:
            verify = self.verify

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "base_sha": base_sha,
                "bundle_b64": bundle_b64,
                "head_sha": head_sha,
            }
        )
        if notes_md is not UNSET:
            field_dict["notes_md"] = notes_md
        if verify is not UNSET:
            field_dict["verify"] = verify

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.publish_run_publish_in_verify_type_0 import (
            PublishRunPublishInVerifyType0,
        )

        d = dict(src_dict)
        base_sha = d.pop("base_sha")

        bundle_b64 = d.pop("bundle_b64")

        head_sha = d.pop("head_sha")

        notes_md = d.pop("notes_md", UNSET)

        def _parse_verify(
            data: object,
        ) -> None | PublishRunPublishInVerifyType0 | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                verify_type_0 = PublishRunPublishInVerifyType0.from_dict(data)

                return verify_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | PublishRunPublishInVerifyType0 | Unset, data)

        verify = _parse_verify(d.pop("verify", UNSET))

        publish_run_publish_in = cls(
            base_sha=base_sha,
            bundle_b64=bundle_b64,
            head_sha=head_sha,
            notes_md=notes_md,
            verify=verify,
        )

        publish_run_publish_in.additional_properties = d
        return publish_run_publish_in

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
