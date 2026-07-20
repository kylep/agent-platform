import pytest
from agentplatform.api.auth import ROLES, role_allows


@pytest.mark.parametrize("role,allowed,ok", [
    ("admin", ("reader",), True),        # admin passes any scope
    ("admin", (), True),                 # admin passes even an empty scope
    ("coder", ("coder", "operator"), True),
    ("operator", ("operator",), True),
    ("reader", ("reader",), True),
    ("reader", ("operator",), False),    # under-privileged
    ("operator", ("coder",), False),
    (None, ("reader",), False),          # unauthenticated
    ("bogus", ("reader",), False),       # unknown role, not listed
])
def test_role_allows(role, allowed, ok):
    assert role_allows(role, allowed) is ok


def test_roles_declared():
    assert set(ROLES) == {"reader", "operator", "coder", "admin"}


async def test_require_admin_still_gates(admin_client):
    # require_admin is now require_role("admin"); the admin session still works.
    assert (await admin_client.get("/api/runs")).status_code == 200
