"""PLATFORM_ADMIN passes ADMIN-gated roles (audited support sessions)."""
import pytest
from fastapi import HTTPException

from quotation_service.auth_helper import require_role


def test_platform_admin_passes_admin_gate():
    dep = require_role(["ADMIN"])
    caller = {"auth_id": "root", "role": "PLATFORM_ADMIN"}
    assert dep(caller) is caller


def test_staff_still_denied():
    dep = require_role(["ADMIN"])
    with pytest.raises(HTTPException) as exc:
        dep({"auth_id": "s", "role": "STAFF"})
    assert exc.value.status_code == 403
