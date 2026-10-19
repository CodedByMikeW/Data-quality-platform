"""
Access codes for Knowledge Search.

Who can see a document is decided by an access code, not an account: nobody
logs in, they type a code. No code means public documents only. The team code
opens team documents (and public ones). Confidential is the one level that
takes two things: the confidential code *and* an ID number (5 digits, typed on
the page's ID pad). Together they open everything - confidential, team and
public. The confidential code on its own still opens team and public, like any
higher code opens the levels under it, but nothing confidential: no text, no
excerpt, no table - the same locked entry anyone else gets.

Inside the app a code grants a *role* (employee / manager / admin), which is
what routers/documents.py already filters on. So the codes don't replace the
role logic, they are the proof that a caller may act as a role: asking to view
as "manager" without the team code (or "admin" without the confidential code and
the ID number) is refused.

This is a small, demo-grade scheme - read the README's note on going live. The
part that matters is that the check runs on the server for every request, so
hiding a document in the page is never the only thing protecting it.
"""
import hmac

from fastapi import HTTPException

from app.config import settings

ROLE_ORDER = ["employee", "manager", "admin"]  # least -> most access

# The access level each privileged role stands for. "employee" is public
# access and needs no code, so it has no entry.
LEVEL_FOR_ROLE = {"manager": "team", "admin": "confidential"}

WRONG_CODE_MESSAGE = "That access code isn't right."
WRONG_ID_MESSAGE = "That ID number isn't right."


def _matches(provided: str, expected: str) -> bool:
    # Constant-time, so response timing can't be used to guess a code digit by
    # digit. Encoded first because compare_digest only accepts ASCII str. A
    # blank configured code never matches: it would mean "no code needed",
    # which would silently unlock the level for everyone.
    expected = expected.strip()
    if not expected:
        return False
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


def _grant(code: str | None, access_id: str | None) -> tuple[str, bool]:
    """
    (role, id_still_needed) for a code and the ID that came with it.

    The ID only matters to the confidential code. With that code and no ID the
    caller is a "manager" (team and public open, confidential stays locked) and
    the ID is still needed; with the right ID they are an "admin"; with a wrong
    one it is a 403, not a quiet downgrade, so a typo can't leave someone
    wondering why confidential documents are missing. A stray ID sent with the
    team code, or with no code, is ignored.
    """
    provided = (code or "").strip()
    if not provided:
        return "employee", False
    # Confidential is checked first: if both codes were ever configured to the
    # same value, the stronger one wins.
    if _matches(provided, settings.confidential_access_code):
        given_id = (access_id or "").strip()
        if not given_id:
            return "manager", True
        if _matches(given_id, settings.confidential_id_number):
            return "admin", False
        raise HTTPException(status_code=403, detail=WRONG_ID_MESSAGE)
    if _matches(provided, settings.team_access_code):
        return "manager", False
    raise HTTPException(status_code=403, detail=WRONG_CODE_MESSAGE)


def role_for_code(code: str | None, access_id: str | None = None) -> str:
    """
    The most access `code` (and `access_id`) grants. No code means "employee"
    (public only); a code that matches nothing is a 403, not a quiet downgrade,
    so a typo can't leave someone wondering why documents are missing.
    """
    return _grant(code, access_id)[0]


def authorize_role(role: str, code: str | None, access_id: str | None = None) -> None:
    """
    Raises 403 unless `code` (with `access_id`, for confidential) lets the caller
    view as `role`. The public role ("employee") never needs a code - and a
    stale or mistyped code sent along with a public request is ignored rather
    than refused, so an out-of-date browser can still see the public documents.
    """
    wanted = ROLE_ORDER.index(role)
    if wanted == 0:
        return
    granted = ROLE_ORDER.index(role_for_code(code, access_id))
    if wanted > granted:
        needs = f"the {LEVEL_FOR_ROLE[role]} access code"
        if role == "admin":
            needs += " and ID number"
        raise HTTPException(status_code=403, detail=f"Viewing as '{role}' needs {needs}.")


def unlock(code: str | None, access_id: str | None = None) -> tuple[str, str, bool]:
    """
    (role, level, needs_id) that a correct code (and ID) grants - what the page's
    Unlock button asks. `needs_id` is True when the code was the confidential
    one and no ID came with it: the page then asks for the ID on its pad.
    """
    if not (code or "").strip():
        raise HTTPException(status_code=400, detail="Enter an access code.")
    role, needs_id = _grant(code, access_id)  # a non-blank code either matches one of the two or raises 403
    return role, LEVEL_FOR_ROLE[role], needs_id
