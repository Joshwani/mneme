from __future__ import annotations

import pytest

from mneme.memory.db import NotesDB
from mneme.memory.notes import (
    add_note,
    delete_note,
    get_note,
    list_notes,
    search_notes,
    update_note,
)


@pytest.fixture()
def notes_db(tmp_path) -> NotesDB:
    db = NotesDB(tmp_path / "notes.db")
    yield db
    db.close()


def test_add_and_get_note(notes_db):
    note = add_note(
        notes_db,
        title="Stripe refund flow",
        body="POST /v1/refunds requires payment_id and amount.",
        tags=["stripe", "payments"],
        scope="finops",
    )

    assert note.note_id.startswith("note_")
    assert note.title == "Stripe refund flow"
    assert sorted(note.tags) == ["payments", "stripe"]
    assert note.scope == "finops"

    fetched = get_note(notes_db, note.note_id)
    assert fetched is not None
    assert fetched.note_id == note.note_id
    assert fetched.body == note.body


def test_add_rejects_empty_title(notes_db):
    with pytest.raises(ValueError):
        add_note(notes_db, title="   ")


def test_tags_dedupe_and_normalize(notes_db):
    note = add_note(notes_db, title="t", body="b", tags=["alpha", "alpha", " beta ", ""])
    assert note.tags == ["alpha", "beta"]


def test_list_notes_orders_by_recency(notes_db):
    a = add_note(notes_db, title="first", body="")
    b = add_note(notes_db, title="second", body="")
    listed = list_notes(notes_db, limit=10)
    assert [n.note_id for n in listed[:2]] == [b.note_id, a.note_id]


def test_search_returns_matches_only(notes_db):
    add_note(notes_db, title="Stripe refund", body="refunds API", tags=["stripe"])
    add_note(notes_db, title="Slack message", body="chat.postMessage", tags=["slack"])

    results = search_notes(notes_db, "refund")
    assert len(results) == 1
    assert "Stripe" in results[0].title


def test_search_filters_by_scope_and_tag(notes_db):
    add_note(notes_db, title="alpha", body="hello", tags=["x"], scope="a")
    add_note(notes_db, title="beta", body="hello", tags=["y"], scope="b")

    in_a = search_notes(notes_db, "hello", scope="a")
    assert [n.title for n in in_a] == ["alpha"]

    with_y = search_notes(notes_db, "hello", tag="y")
    assert [n.title for n in with_y] == ["beta"]


def test_update_preserves_untouched_fields(notes_db):
    note = add_note(notes_db, title="orig", body="body1", tags=["a", "b"], scope="x")
    updated = update_note(notes_db, note.note_id, body="body2")

    assert updated.title == "orig"
    assert updated.body == "body2"
    assert sorted(updated.tags) == ["a", "b"]
    assert updated.scope == "x"
    assert updated.updated_at >= note.updated_at


def test_update_unknown_note_raises(notes_db):
    with pytest.raises(KeyError):
        update_note(notes_db, "note_does_not_exist", title="x")


def test_delete_note(notes_db):
    note = add_note(notes_db, title="gone", body="")
    assert delete_note(notes_db, note.note_id) is True
    assert get_note(notes_db, note.note_id) is None
    assert delete_note(notes_db, note.note_id) is False
