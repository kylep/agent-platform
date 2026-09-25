from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

if TYPE_CHECKING:
    from ..models.tool_quick_edit_in_files import ToolQuickEditInFiles


T = TypeVar("T", bound="ToolQuickEditIn")


@_attrs_define
class ToolQuickEditIn:
    """
    Attributes:
        files (ToolQuickEditInFiles):
    """

    files: ToolQuickEditInFiles
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        files = self.files.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "files": files,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.tool_quick_edit_in_files import ToolQuickEditInFiles

        d = dict(src_dict)
        files = ToolQuickEditInFiles.from_dict(d.pop("files"))

        tool_quick_edit_in = cls(
            files=files,
        )

        tool_quick_edit_in.additional_properties = d
        return tool_quick_edit_in

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
