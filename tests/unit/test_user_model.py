from flowmate.db.models import User


def test_user_model_shape() -> None:
    table = User.__table__

    assert User.__tablename__ == "users"
    assert table.c.id.primary_key is True
    assert table.c.telegram_user_id.unique is True
    assert table.c.telegram_user_id.nullable is False
    assert table.c.is_active.nullable is False
    assert table.c.created_at.nullable is False
    assert table.c.updated_at.nullable is False
