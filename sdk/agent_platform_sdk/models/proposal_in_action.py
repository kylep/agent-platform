from enum import Enum


class ProposalInAction(str, Enum):
    GET = "get"
    WITHDRAW = "withdraw"

    def __str__(self) -> str:
        return str(self.value)
