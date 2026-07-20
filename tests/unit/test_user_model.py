from typing import cast

from sqlalchemy import String, Table

from flowmate.db.models import User


def test_user_model_shape() -> None:
    table = cast(Table, User.__table__)
    display_name_type = cast(String, table.c.display_name.type)

    assert User.__tablename__ == "users"
    assert table.c.id.primary_key is True
    assert table.c.telegram_user_id.nullable is True
    assert table.c.display_name.nullable is True
    assert display_name_type.length == 255
    assert table.c.is_active.nullable is False
    assert table.c.created_at.nullable is False
    assert table.c.updated_at.nullable is False
    assert "users_telegram_user_id_key" in {
        constraint.name for constraint in table.constraints
    }
    assert "ck_users_telegram_user_id_positive" in {
        constraint.name for constraint in table.constraints
    }
