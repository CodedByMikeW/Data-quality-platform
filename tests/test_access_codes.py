"""
Access codes: who may open which Knowledge Search documents.

Every document is listed and searchable by everyone; the code decides which
ones open. No code means public documents open. The team code adds team
documents. Confidential takes two things - the confidential code and the
5-digit ID number - and together they open everything; the code on its own
opens team and public but nothing confidential. A document the code doesn't
open still turns up in the list and in search, but as a locked entry - its
name, type, level and date, and nothing from inside it. The check runs on the
server for every request, so these tests talk to the API directly - as
someone who never loads the page - and try to get around the codes.

Embeddings are stubbed: what's under test is permissions, not search quality.
"""
import io

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app import access, models
from app.config import Settings, settings
from app.database import SessionLocal
from app.routers.documents import _ROLE_MAX_LEVEL

TEAM = settings.team_access_code
CONFIDENTIAL = settings.confidential_access_code
ID = settings.confidential_id_number
WRONG = "000000"
WRONG_ID = "00000"


def _stub_embed(texts):
    return [[float(len(t) % 7 + 1), 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0] for t in texts]


@pytest.fixture(autouse=True)
def stub_embeddings(monkeypatch):
    monkeypatch.setattr("app.document_store.embed_texts", _stub_embed)
    monkeypatch.setattr("app.routers.documents.embed_texts", _stub_embed)


def _as(code, access_id=None):
    headers = {"X-Access-Code": code} if code else {}
    if access_id:
        headers["X-Access-Id"] = access_id
    return headers


def _as_confidential():
    """The confidential code together with its ID number: the whole key."""
    return _as(CONFIDENTIAL, ID)


def _upload(client, filename, text, level):
    files = {"file": (filename, io.BytesIO(text.encode()), "text/plain")}
    resp = client.post("/documents", files=files, data={"access_level": level})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _locked_by_name(resp):
    """{filename: is it locked?} for a list or search response."""
    assert resp.status_code == 200, resp.text
    return {item["filename"]: item["locked"] for item in resp.json()}


# What a locked entry may say - and so everything it ever says. No text, excerpt, size or score.
LOCKED_LIST_KEYS = {"id", "filename", "content_type", "access_level", "version", "uploaded_at", "locked"}
LOCKED_HIT_KEYS = {"id", "filename", "content_type", "access_level", "locked"}


@pytest.fixture
def docs(anonymous_client):
    """One document per level. Putting a document in needs no code - only reading is gated."""
    return {
        level: _upload(anonymous_client, f"{level}-doc.txt", f"The {level} handbook on quarterly budgets.", level)
        for level in ("public", "team", "confidential")
    }


PUBLIC, TEAM_DOC, CONF_DOC = "public-doc.txt", "team-doc.txt", "confidential-doc.txt"


# ------------------------------------------------------------ the codes

def test_the_default_codes_are_the_ones_chosen_for_the_demo():
    # Read from the field definitions, so a local .env override can't hide a changed default.
    assert Settings.model_fields["team_access_code"].default == "280010"
    assert Settings.model_fields["confidential_access_code"].default == "938032"
    assert Settings.model_fields["show_access_hint"].default is True


def test_the_two_codes_are_different():
    assert TEAM != CONFIDENTIAL


def test_the_role_order_matches_the_roles_the_documents_router_filters_on():
    assert access.ROLE_ORDER == list(_ROLE_MAX_LEVEL)


@pytest.mark.parametrize("code", [None, "", "   "])
def test_no_code_means_public_access_only(code):
    assert access.role_for_code(code) == "employee"


def test_the_team_code_grants_manager_and_the_confidential_code_with_its_id_grants_admin():
    assert access.role_for_code(TEAM) == "manager"
    assert access.role_for_code(CONFIDENTIAL, ID) == "admin"
    assert access.role_for_code(f"  {TEAM}\n") == "manager"  # stray whitespace from a copy-paste
    assert access.role_for_code(f" {CONFIDENTIAL}\n", f"\t{ID} ") == "admin"


@pytest.mark.parametrize("code", [WRONG, TEAM[:-1], TEAM + "0", "280 010", "é", "٢٨٠٠١٠", "x" * 5000])
def test_a_wrong_code_is_refused_not_quietly_downgraded(code):
    with pytest.raises(HTTPException) as err:
        access.role_for_code(code)
    assert err.value.status_code == 403
    assert err.value.detail == access.WRONG_CODE_MESSAGE


def test_a_blank_configured_code_can_never_be_matched(monkeypatch):
    monkeypatch.setattr(settings, "team_access_code", "")
    assert access.role_for_code("") == "employee"  # "no code" stays "no code", it doesn't unlock team
    with pytest.raises(HTTPException):
        access.role_for_code("anything")
    with pytest.raises(HTTPException):
        access.authorize_role("manager", None)
    access.authorize_role("manager", CONFIDENTIAL)  # the other code still works


def test_if_both_codes_were_configured_the_same_the_stronger_one_wins(monkeypatch):
    monkeypatch.setattr(settings, "team_access_code", CONFIDENTIAL)
    assert access.role_for_code(CONFIDENTIAL, ID) == "admin"
    assert access.role_for_code(CONFIDENTIAL) == "manager"  # ...and without the ID it is still the team code


@pytest.mark.parametrize(
    "role, code, access_id, allowed",
    [
        ("employee", None, None, True),
        ("employee", TEAM, None, True),
        ("employee", WRONG, None, True),  # a stale code mustn't hide the public documents
        ("employee", CONFIDENTIAL, WRONG_ID, True),  # nor a stale code with a stale ID
        ("manager", None, None, False),
        ("manager", WRONG, None, False),
        ("manager", TEAM, None, True),
        ("manager", TEAM, WRONG_ID, True),  # the ID belongs to the confidential code; beside the team code it is ignored
        ("manager", CONFIDENTIAL, None, True),  # the confidential code opens team documents too
        ("manager", CONFIDENTIAL, ID, True),
        ("admin", None, None, False),
        ("admin", None, ID, False),  # an ID is nothing without the code
        ("admin", WRONG, ID, False),
        ("admin", TEAM, None, False),
        ("admin", TEAM, ID, False),  # the right ID can't make the team code a confidential one
        ("admin", CONFIDENTIAL, None, False),  # the code alone is not enough for confidential
        ("admin", CONFIDENTIAL, WRONG_ID, False),
        ("admin", CONFIDENTIAL, ID, True),  # code and ID together
    ],
)
def test_which_code_lets_you_view_as_which_role(role, code, access_id, allowed):
    if allowed:
        access.authorize_role(role, code, access_id)
    else:
        with pytest.raises(HTTPException) as err:
            access.authorize_role(role, code, access_id)
        assert err.value.status_code == 403


def test_the_refusal_says_which_code_is_needed():
    with pytest.raises(HTTPException) as err:
        access.authorize_role("manager", None)
    assert "team access code" in err.value.detail

    with pytest.raises(HTTPException) as err:
        access.authorize_role("admin", TEAM)
    assert "confidential access code" in err.value.detail

    with pytest.raises(HTTPException) as err:
        access.authorize_role("admin", CONFIDENTIAL)  # right code, no ID yet
    assert err.value.detail == "Viewing as 'admin' needs the confidential access code and ID number."


# ----------------------------------------------------- unlock + hint box


def test_unlock_tells_the_page_what_a_code_opens(anonymous_client):
    team = anonymous_client.post("/documents/access/unlock", json={"code": TEAM})
    assert team.status_code == 200
    assert team.json() == {"role": "manager", "level": "team", "needs_id": False}

    # The confidential code alone is only half the key: it opens team, and says an ID is still needed.
    half = anonymous_client.post("/documents/access/unlock", json={"code": f" {CONFIDENTIAL} "})
    assert half.status_code == 200
    assert half.json() == {"role": "manager", "level": "team", "needs_id": True}

    full = anonymous_client.post("/documents/access/unlock", json={"code": f" {CONFIDENTIAL} ", "id": f" {ID} "})
    assert full.json() == {"role": "admin", "level": "confidential", "needs_id": False}


def test_unlock_refuses_a_wrong_code(anonymous_client):
    resp = anonymous_client.post("/documents/access/unlock", json={"code": WRONG})
    assert resp.status_code == 403
    assert resp.json()["detail"] == access.WRONG_CODE_MESSAGE


def test_unlock_asks_for_a_code_when_none_is_given(anonymous_client):
    blank = anonymous_client.post("/documents/access/unlock", json={"code": "   "})
    assert blank.status_code == 400
    assert blank.json()["detail"] == "Enter an access code."
    assert anonymous_client.post("/documents/access/unlock", json={}).status_code == 422


def test_the_hint_box_shows_both_codes_and_the_confidential_id_by_default(anonymous_client):
    resp = anonymous_client.get("/documents/access/hint")
    assert resp.status_code == 200
    assert resp.json() == {
        "enabled": True,
        "team_code": TEAM,
        "confidential_code": CONFIDENTIAL,
        "confidential_id": ID,
    }


def test_the_hint_can_be_switched_off_and_then_reveals_nothing(anonymous_client, monkeypatch):
    monkeypatch.setattr(settings, "show_access_hint", False)
    resp = anonymous_client.get("/documents/access/hint")
    assert resp.json() == {"enabled": False, "team_code": None, "confidential_code": None, "confidential_id": None}
    assert TEAM not in resp.text and CONFIDENTIAL not in resp.text and ID not in resp.text


# ------------------------------------------------------- the list


def test_the_list_shows_everything_and_locks_what_the_code_does_not_open(anonymous_client, docs):
    c = anonymous_client
    assert _locked_by_name(c.get("/documents")) == {PUBLIC: False, TEAM_DOC: True, CONF_DOC: True}
    assert _locked_by_name(c.get("/documents", params={"role": "manager"}, headers=_as(TEAM))) == {
        PUBLIC: False,
        TEAM_DOC: False,
        CONF_DOC: True,
    }
    assert _locked_by_name(c.get("/documents", params={"role": "admin"}, headers=_as_confidential())) == {
        PUBLIC: False,
        TEAM_DOC: False,
        CONF_DOC: False,
    }
    # The confidential code also opens team documents when the page asks for the team view.
    assert _locked_by_name(c.get("/documents", params={"role": "manager"}, headers=_as(CONFIDENTIAL))) == {
        PUBLIC: False,
        TEAM_DOC: False,
        CONF_DOC: True,
    }


def test_a_locked_entry_says_what_it_is_and_nothing_from_inside_it(anonymous_client, docs):
    items = {d["filename"]: d for d in anonymous_client.get("/documents").json()}

    for name in (TEAM_DOC, CONF_DOC):
        assert set(items[name]) == LOCKED_LIST_KEYS, name
    assert items[CONF_DOC]["access_level"] == "confidential"  # enough to tell which code opens it
    assert "char_count" not in items[CONF_DOC]

    # An open one is the full entry.
    assert {"char_count", "is_latest"} <= set(items[PUBLIC]) and items[PUBLIC]["locked"] is False


def test_nothing_from_inside_a_locked_document_is_in_the_list(anonymous_client):
    _upload(anonymous_client, "figures.txt", "ZEBRA-4417 payroll: Alice earns 120000 a year.", "confidential")
    raw = anonymous_client.get("/documents").text

    assert "figures.txt" in raw
    for inside in ("ZEBRA", "Alice", "120000", "payroll"):
        assert inside not in raw, inside


@pytest.mark.parametrize(
    "role, code, message",
    [
        ("manager", None, "team access code"),
        ("manager", WRONG, "isn't right"),
        ("admin", None, "confidential access code"),
        ("admin", TEAM, "confidential access code"),
        ("admin", WRONG, "isn't right"),
        ("admin", CONFIDENTIAL, "ID number"),  # the code alone is not the whole key
    ],
)
def test_asking_to_view_as_a_higher_role_without_its_code_is_refused(anonymous_client, docs, role, code, message):
    resp = anonymous_client.get("/documents", params={"role": role}, headers=_as(code))
    assert resp.status_code == 403
    assert message in resp.json()["detail"]


def test_a_stale_code_does_not_lock_the_public_documents(anonymous_client, docs):
    listed = _locked_by_name(anonymous_client.get("/documents", headers=_as(WRONG)))
    assert listed == {PUBLIC: False, TEAM_DOC: True, CONF_DOC: True}


def test_a_code_in_the_url_is_not_accepted(anonymous_client, docs):
    resp = anonymous_client.get(
        "/documents", params={"role": "manager", "access_code": TEAM, "x-access-code": TEAM, "code": TEAM}
    )
    assert resp.status_code == 403


def test_header_names_are_case_insensitive(anonymous_client, docs):
    resp = anonymous_client.get("/documents", params={"role": "manager"}, headers={"x-access-code": TEAM})
    assert _locked_by_name(resp) == {PUBLIC: False, TEAM_DOC: False, CONF_DOC: True}


def test_an_unknown_role_is_still_a_400_whatever_the_code(anonymous_client):
    resp = anonymous_client.get("/documents", params={"role": "hacker"}, headers=_as(CONFIDENTIAL))
    assert resp.status_code == 400


# ------------------------------------------------- opening one document


def test_a_document_opens_only_with_the_code_for_its_level(anonymous_client, docs):
    c = anonymous_client
    public_id, team_id, conf_id = (docs[level]["id"] for level in ("public", "team", "confidential"))

    assert c.get(f"/documents/{public_id}").status_code == 200  # no code needed
    assert c.get(f"/documents/{team_id}").status_code == 403
    assert c.get(f"/documents/{conf_id}").status_code == 403

    # Naming a role proves nothing - the matching code does.
    assert c.get(f"/documents/{team_id}", params={"role": "manager"}).status_code == 403
    opened = c.get(f"/documents/{team_id}", params={"role": "manager"}, headers=_as(TEAM))
    assert opened.status_code == 200
    assert "team handbook" in opened.json()["text_content"]

    # The team code doesn't reach confidential documents, however it is asked.
    assert c.get(f"/documents/{conf_id}", params={"role": "manager"}, headers=_as(TEAM)).status_code == 403
    assert c.get(f"/documents/{conf_id}", params={"role": "admin"}, headers=_as(TEAM)).status_code == 403
    assert c.get(f"/documents/{conf_id}", params={"role": "admin"}, headers=_as_confidential()).status_code == 200


def test_the_confidential_code_with_its_id_opens_team_documents_too(anonymous_client, docs):
    resp = anonymous_client.get(f"/documents/{docs['team']['id']}", params={"role": "admin"}, headers=_as_confidential())
    assert resp.status_code == 200
    # ...and with the code alone it opens them as the team view.
    alone = anonymous_client.get(f"/documents/{docs['team']['id']}", params={"role": "manager"}, headers=_as(CONFIDENTIAL))
    assert alone.status_code == 200


def test_a_document_that_does_not_exist_is_a_404_for_anyone_cleared_to_ask(anonymous_client):
    assert anonymous_client.get("/documents/99999").status_code == 404


def test_history_only_lists_the_versions_the_code_opens(anonymous_client):
    c = anonymous_client
    v1 = _upload(c, "policy.txt", "policy v1", "public")
    v2 = _upload(c, "policy.txt", "policy v2", "team")
    history = f"/documents/{v2['id']}/history"

    assert c.get(history).status_code == 403  # v2 itself is a team document
    assert c.get(history, params={"role": "manager"}).status_code == 403
    cleared = c.get(history, params={"role": "manager"}, headers=_as(TEAM))
    assert [v["version"] for v in cleared.json()] == [2, 1]

    # From the public version, a public visitor sees the public part of the history only.
    public_view = c.get(f"/documents/{v1['id']}/history")
    assert [v["version"] for v in public_view.json()] == [1]


# ------------------------------------------------------------- search


@pytest.mark.parametrize("endpoint", ["search", "semantic-search", "hybrid-search"])
def test_every_search_mode_finds_everything_and_locks_what_the_code_does_not_open(anonymous_client, docs, endpoint):
    c = anonymous_client
    url = f"/documents/{endpoint}"
    query = {"q": "handbook"}

    assert _locked_by_name(c.get(url, params=query)) == {PUBLIC: False, TEAM_DOC: True, CONF_DOC: True}
    assert c.get(url, params={**query, "role": "admin"}, headers=_as(TEAM)).status_code == 403
    assert c.get(url, params={**query, "role": "manager"}).status_code == 403
    assert _locked_by_name(c.get(url, params={**query, "role": "manager"}, headers=_as(TEAM))) == {
        PUBLIC: False,
        TEAM_DOC: False,
        CONF_DOC: True,
    }
    assert _locked_by_name(c.get(url, params={**query, "role": "admin"}, headers=_as_confidential())) == {
        PUBLIC: False,
        TEAM_DOC: False,
        CONF_DOC: False,
    }


@pytest.mark.parametrize("endpoint", ["search", "semantic-search", "hybrid-search"])
def test_a_locked_hit_says_what_matched_and_nothing_from_inside_it(anonymous_client, docs, endpoint):
    hits = {h["filename"]: h for h in anonymous_client.get(f"/documents/{endpoint}", params={"q": "handbook"}).json()}

    assert set(hits[CONF_DOC]) == LOCKED_HIT_KEYS
    assert set(hits[TEAM_DOC]) == LOCKED_HIT_KEYS
    assert hits[CONF_DOC]["access_level"] == "confidential"

    # The one the caller may open is the full result, excerpt and citation included.
    assert hits[PUBLIC]["locked"] is False and hits[PUBLIC]["snippet"] and hits[PUBLIC]["citation"]


@pytest.mark.parametrize(
    "endpoint, query",
    [("search", "ZEBRA-4417"), ("semantic-search", "payroll"), ("hybrid-search", "ZEBRA-4417")],
)
def test_nothing_from_inside_a_locked_document_comes_back_from_search(anonymous_client, endpoint, query):
    _upload(anonymous_client, "figures.txt", "ZEBRA-4417 payroll: Alice earns 120000 a year.", "confidential")
    resp = anonymous_client.get(f"/documents/{endpoint}", params={"q": query})

    assert [(h["filename"], h["locked"]) for h in resp.json()] == [("figures.txt", True)]
    for inside in ("ZEBRA", "Alice", "120000", "earns", "snippet", "citation", "chunk", "similarity", "score"):
        assert inside not in resp.text, inside


def test_search_finds_a_locked_document_by_what_is_inside_it(anonymous_client):
    # That is the point: anyone can search everything. The code is for opening what is found.
    _upload(anonymous_client, "figures.txt", "ZEBRA-4417 payroll figures.", "confidential")
    _upload(anonymous_client, "unrelated.txt", "Nothing to do with it.", "public")

    hits = anonymous_client.get("/documents/search", params={"q": "zebra-4417"}).json()
    assert [(h["filename"], h["locked"]) for h in hits] == [("figures.txt", True)]


def test_locked_and_open_documents_are_ranked_together_by_relevance(anonymous_client):
    _upload(anonymous_client, "mentions-once.txt", "One note about the budget.", "public")
    _upload(anonymous_client, "mentions-lots.txt", "budget budget budget budget budget", "team")

    hits = anonymous_client.get("/documents/search", params={"q": "budget"}).json()
    assert [(h["filename"], h["locked"]) for h in hits] == [("mentions-lots.txt", True), ("mentions-once.txt", False)]


def test_a_search_that_only_matches_locked_documents_is_not_logged_as_empty(anonymous_client):
    _upload(anonymous_client, "figures.txt", "ZEBRA-4417 payroll figures.", "confidential")
    anonymous_client.get("/documents/search", params={"q": "zebra-4417"})  # finds it, locked
    anonymous_client.get("/documents/search", params={"q": "xyzzy-nothing-here"})  # finds nothing

    summary = anonymous_client.get(
        "/documents/analytics/summary", params={"role": "manager"}, headers=_as(TEAM)
    ).json()
    assert summary["zero_result_queries"] == [{"query": "xyzzy-nothing-here", "count": 1}]
    assert summary["total_searches"] == 2


def test_the_id_in_a_locked_entry_opens_only_with_the_code(anonymous_client, docs):
    c = anonymous_client
    team_id = next(d["id"] for d in c.get("/documents").json() if d["filename"] == TEAM_DOC)

    assert c.get(f"/documents/{team_id}").status_code == 403
    assert c.get(f"/documents/{team_id}/history").status_code == 403
    assert c.get(f"/documents/{team_id}", params={"role": "manager"}, headers=_as(WRONG)).status_code == 403
    assert c.get(f"/documents/{team_id}", params={"role": "manager"}, headers=_as(TEAM)).status_code == 200


def test_a_refused_search_is_not_logged(anonymous_client, docs):
    anonymous_client.get("/documents/search", params={"q": "handbook", "role": "admin"}, headers=_as(WRONG))
    db = SessionLocal()
    try:
        assert db.query(models.SearchQueryLog).count() == 0
    finally:
        db.close()


@pytest.mark.parametrize(
    "method, url", [("get", "/documents/analytics/summary"), ("delete", "/documents/search-logs")]
)
def test_search_analytics_and_clearing_the_log_need_a_code_too(anonymous_client, method, url):
    send = getattr(anonymous_client, method)
    assert send(url).status_code == 403  # public visitors never see them
    assert send(url, params={"role": "manager"}).status_code == 403  # naming the role isn't enough any more
    assert send(url, params={"role": "manager"}, headers=_as(WRONG)).status_code == 403
    assert send(url, params={"role": "admin"}, headers=_as(TEAM)).status_code == 403
    assert send(url, params={"role": "manager"}, headers=_as(TEAM)).status_code == 200
    assert send(url, params={"role": "admin"}, headers=_as(CONFIDENTIAL)).status_code == 403  # the code alone is not enough
    assert send(url, params={"role": "admin"}, headers=_as_confidential()).status_code == 200


# --------------------------------------------------------------- writes


def test_putting_a_document_in_needs_no_code_only_reading_is_gated(anonymous_client):
    # Documented behaviour (see the README): anyone may add a document at any
    # level, but opening it still takes the code for that level.
    saved = _upload(anonymous_client, "note.txt", "a confidential note", "confidential")
    assert anonymous_client.get(f"/documents/{saved['id']}").status_code == 403


# --------------------------------------------------- the confidential ID number
#
# Confidential is the one level that takes two things: the confidential code and
# a 5-digit ID number. The code alone is a team key with the ID still to come.


def test_the_default_id_number_is_the_one_chosen_for_the_demo():
    assert Settings.model_fields["confidential_id_number"].default == "69712"


def test_the_id_number_is_not_the_same_thing_as_either_code():
    assert ID not in (TEAM, CONFIDENTIAL) and len(ID) == 5 and ID.isdigit()


@pytest.mark.parametrize("value", ["69712", "00123", "00000", " 69712 ", "69712\n"])
def test_the_id_number_setting_takes_exactly_five_digits(value):
    assert Settings(confidential_id_number=value).confidential_id_number == value.strip()


@pytest.mark.parametrize(
    "value", ["", "   ", "1234", "123456", "abcde", "6971a", "69 712", "-6971", "6971.", "٦٩٧١٢", "²³⁴⁵⁶"]
)
def test_an_id_number_that_is_not_five_digits_stops_the_app_starting(value, monkeypatch):
    # Better to fail loudly at start-up than to lock everyone out of Confidential: the pad has five dots.
    with pytest.raises(ValidationError) as err:
        Settings(confidential_id_number=value)
    assert "exactly 5 digits" in str(err.value)

    monkeypatch.setenv("CONFIDENTIAL_ID_NUMBER", value)  # the way a host sets it
    with pytest.raises(ValidationError):
        Settings()


def test_the_id_number_can_be_set_from_the_environment(monkeypatch):
    monkeypatch.setenv("CONFIDENTIAL_ID_NUMBER", "13579")
    assert Settings().confidential_id_number == "13579"


def test_the_confidential_code_alone_is_a_team_key_with_the_id_still_to_come():
    for blank in (None, "", "   "):  # an empty ID box is "no ID yet", not a wrong guess
        assert access.role_for_code(CONFIDENTIAL, blank) == "manager"
    access.authorize_role("manager", CONFIDENTIAL)
    with pytest.raises(HTTPException) as err:
        access.authorize_role("admin", CONFIDENTIAL)
    assert err.value.status_code == 403


@pytest.mark.parametrize("wrong", [WRONG_ID, ID[:-1], ID + "0", "697120", "6 9712", "٦٩٧١٢", "x" * 5000, ID + "\x00"])
def test_a_wrong_id_is_refused_not_quietly_downgraded(wrong):
    with pytest.raises(HTTPException) as err:
        access.role_for_code(CONFIDENTIAL, wrong)
    assert err.value.status_code == 403
    assert err.value.detail == access.WRONG_ID_MESSAGE


def test_the_id_means_nothing_without_the_confidential_code():
    assert access.role_for_code(None, ID) == "employee"
    assert access.role_for_code("", WRONG_ID) == "employee"
    assert access.role_for_code(TEAM, ID) == "manager"
    assert access.role_for_code(TEAM, WRONG_ID) == "manager"  # beside the team code it is ignored
    with pytest.raises(HTTPException) as err:
        access.role_for_code(WRONG, ID)
    assert err.value.detail == access.WRONG_CODE_MESSAGE  # the code is checked first


def test_a_blank_configured_id_means_nobody_reaches_confidential(monkeypatch):
    # Mirrors the blank-code rule: a missing setting must lock the level, never unlock it.
    monkeypatch.setattr(settings, "confidential_id_number", "")
    assert access.role_for_code(CONFIDENTIAL) == "manager"
    with pytest.raises(HTTPException):
        access.role_for_code(CONFIDENTIAL, "12345")
    with pytest.raises(HTTPException):
        access.authorize_role("admin", CONFIDENTIAL, "")
    with pytest.raises(HTTPException):
        access.authorize_role("admin", CONFIDENTIAL, "   ")


def test_unlock_asks_for_the_id_and_checks_it(anonymous_client):
    def post(body):
        return anonymous_client.post("/documents/access/unlock", json=body)

    wrong = post({"code": CONFIDENTIAL, "id": WRONG_ID})
    assert wrong.status_code == 403 and wrong.json()["detail"] == access.WRONG_ID_MESSAGE

    for body in ({"code": CONFIDENTIAL, "id": ""}, {"code": CONFIDENTIAL, "id": "  "}, {"code": CONFIDENTIAL, "id": None}):
        half = post(body)
        assert half.status_code == 200 and half.json()["needs_id"] is True, body

    # beside the team code an ID is ignored; and it can't stand in for a code
    assert post({"code": TEAM, "id": WRONG_ID}).json() == {"role": "manager", "level": "team", "needs_id": False}
    assert post({"code": "  ", "id": ID}).status_code == 400
    wrong_code = post({"code": WRONG, "id": ID})
    assert wrong_code.status_code == 403 and wrong_code.json()["detail"] == access.WRONG_CODE_MESSAGE
    assert post({"code": CONFIDENTIAL, "id": 69712}).status_code == 422  # the page sends it as text


def test_the_confidential_code_without_its_id_opens_nothing_confidential(anonymous_client, docs):
    c = anonymous_client
    conf_id = docs["confidential"]["id"]

    # As the team view it lists everything and locks the confidential document.
    listed = c.get("/documents", params={"role": "manager"}, headers=_as(CONFIDENTIAL))
    assert _locked_by_name(listed) == {PUBLIC: False, TEAM_DOC: False, CONF_DOC: True}

    # The confidential view is refused, and the refusal says what is missing.
    refused = c.get("/documents", params={"role": "admin"}, headers=_as(CONFIDENTIAL))
    assert refused.status_code == 403 and "ID number" in refused.json()["detail"]

    for role in ("manager", "admin"):
        for suffix in ("", "/history"):
            resp = c.get(f"/documents/{conf_id}{suffix}", params={"role": role}, headers=_as(CONFIDENTIAL))
            assert resp.status_code == 403, (role, suffix)
            assert "confidential handbook" not in resp.text


@pytest.mark.parametrize(
    "endpoint, query",
    [("search", "ZEBRA-4417"), ("semantic-search", "payroll"), ("hybrid-search", "ZEBRA-4417")],
)
def test_without_its_id_the_confidential_code_still_only_gets_a_locked_entry_from_search(
    anonymous_client, endpoint, query
):
    _upload(anonymous_client, "figures.txt", "ZEBRA-4417 payroll: Alice earns 120000 a year.", "confidential")
    resp = anonymous_client.get(
        f"/documents/{endpoint}", params={"q": query, "role": "manager"}, headers=_as(CONFIDENTIAL)
    )

    assert [(h["filename"], h["locked"]) for h in resp.json()] == [("figures.txt", True)]
    for inside in ("ZEBRA", "Alice", "120000", "earns", "snippet", "citation", "chunk", "similarity", "score"):
        assert inside not in resp.text, inside


def test_with_the_code_and_its_id_a_confidential_document_opens_everywhere(anonymous_client, docs):
    c = anonymous_client
    conf_id = docs["confidential"]["id"]
    headers = _as_confidential()

    opened = c.get(f"/documents/{conf_id}", params={"role": "admin"}, headers=headers)
    assert opened.status_code == 200 and "confidential handbook" in opened.json()["text_content"]
    assert c.get(f"/documents/{conf_id}/history", params={"role": "admin"}, headers=headers).status_code == 200

    hits = c.get("/documents/search", params={"q": "handbook", "role": "admin"}, headers=headers).json()
    assert {h["filename"]: h["locked"] for h in hits}[CONF_DOC] is False
    assert [h for h in hits if h["filename"] == CONF_DOC][0]["snippet"]


def _id_endpoints(confidential_text_id, confidential_csv_id):
    """(method, url, params) for everything that reads the code - each must read the ID too."""
    return [
        ("get", "/documents", {}),
        ("get", "/documents/search", {"q": "handbook"}),
        ("get", "/documents/semantic-search", {"q": "handbook"}),
        ("get", "/documents/hybrid-search", {"q": "handbook"}),
        ("get", "/documents/analytics/summary", {}),
        ("delete", "/documents/search-logs", {}),
        ("get", f"/documents/{confidential_text_id}", {}),
        ("get", f"/documents/{confidential_text_id}/history", {}),
        ("get", f"/documents/{confidential_csv_id}/table", {}),
    ]


def _endpoints_for(client, docs, level):
    """The nine endpoints, with the document ones pointed at documents saved at `level`."""
    csv_doc = _upload(client, f"{level}-table.csv", "id,name\n1,Ada\n", level)
    return _id_endpoints(docs[level]["id"], csv_doc["id"])


@pytest.mark.parametrize("index", range(9))
def test_every_endpoint_that_checks_the_code_checks_the_id_as_well(anonymous_client, docs, index):
    method, url, params = _endpoints_for(anonymous_client, docs, "confidential")[index]
    send = getattr(anonymous_client, method)
    admin = {**params, "role": "admin"}

    assert send(url, params=admin, headers=_as(CONFIDENTIAL)).status_code == 403  # the code alone
    wrong = send(url, params=admin, headers=_as(CONFIDENTIAL, WRONG_ID))
    assert wrong.status_code == 403 and wrong.json()["detail"] == access.WRONG_ID_MESSAGE
    assert send(url, params=admin, headers=_as(None, ID)).status_code == 403  # the ID alone
    assert send(url, params=admin, headers=_as(TEAM, ID)).status_code == 403  # ...or beside the team code
    assert send(url, params=admin, headers=_as_confidential()).status_code == 200  # the whole key


@pytest.mark.parametrize("index", range(9))
def test_a_wrong_id_is_a_403_even_when_only_the_team_view_is_asked_for(anonymous_client, docs, index):
    # A typo'd ID beside the confidential code is refused outright - it doesn't quietly fall back to team.
    method, url, params = _endpoints_for(anonymous_client, docs, "team")[index]
    resp = getattr(anonymous_client, method)(
        url, params={**params, "role": "manager"}, headers=_as(CONFIDENTIAL, WRONG_ID)
    )
    assert resp.status_code == 403 and resp.json()["detail"] == access.WRONG_ID_MESSAGE


def test_a_stale_id_does_not_lock_the_public_documents(anonymous_client, docs):
    listed = _locked_by_name(anonymous_client.get("/documents", headers=_as(CONFIDENTIAL, WRONG_ID)))
    assert listed == {PUBLIC: False, TEAM_DOC: True, CONF_DOC: True}


def test_the_id_is_read_from_a_header_not_the_url(anonymous_client, docs):
    resp = anonymous_client.get(
        "/documents",
        params={"role": "admin", "access_id": ID, "id": ID, "x-access-id": ID, "id_number": ID},
        headers=_as(CONFIDENTIAL),
    )
    assert resp.status_code == 403


def test_the_id_header_name_is_case_insensitive(anonymous_client, docs):
    resp = anonymous_client.get(
        "/documents", params={"role": "admin"}, headers={"x-access-code": CONFIDENTIAL, "x-access-id": ID}
    )
    assert _locked_by_name(resp) == {PUBLIC: False, TEAM_DOC: False, CONF_DOC: False}


def test_every_endpoint_that_takes_the_code_takes_the_id_too(anonymous_client):
    # A guard against adding a document endpoint that checks the code but forgets the ID.
    spec = anonymous_client.get("/openapi.json").json()
    seen = []
    for path, methods in spec["paths"].items():
        for method, operation in methods.items():
            headers = {p["name"] for p in operation.get("parameters", []) if p["in"] == "header"}
            if "X-Access-Code" in headers:
                assert "X-Access-Id" in headers, (method, path)
                seen.append((method, path))
    assert len(seen) == 9, seen


def test_the_id_is_not_written_down_anywhere_a_visitor_can_read(anonymous_client, docs):
    anonymous_client.get("/documents/search", params={"q": "handbook", "role": "admin"}, headers=_as_confidential())
    log = anonymous_client.get("/documents/analytics/summary", params={"role": "admin"}, headers=_as_confidential())
    assert ID not in log.text and CONFIDENTIAL not in log.text
    for path in ("/documents", "/health", "/limits", "/openapi.json"):
        assert ID not in anonymous_client.get(path).text, path
