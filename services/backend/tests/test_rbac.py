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
    # `relay` satisfies only the allow-lists that name it (the Relay routes);
    # it is not a rung of a hierarchy.
    ("relay", ("relay", "operator"), True),
    ("relay", ("operator",), False),
    ("relay", ("reader",), False),
])
def test_role_allows(role, allowed, ok):
    assert role_allows(role, allowed) is ok


def test_roles_declared():
    assert set(ROLES) == {"reader", "annotator", "operator", "coder", "admin",
                          "tools", "relay", "dev"}


def test_dev_is_a_run_profile_not_an_api_scope():
    """`dev` (docs/design/24) decides what a run GETS, not what answers it:
    like `tools`, it is in none of the endpoint allow-lists."""
    from agentplatform.api import auth
    lists = [v for k, v in vars(auth).items()
             if k.endswith("_ROLES") and isinstance(v, tuple)]
    assert lists, "no allow-lists found"
    assert all("dev" not in roles for roles in lists)


async def test_require_admin_still_gates(admin_client):
    # require_admin is now require_role("admin"); the admin session still works.
    assert (await admin_client.get("/api/runs")).status_code == 200
