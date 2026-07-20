from collections.abc import AsyncGenerator
from typing import cast

import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.requests import Request

from flowmate.api.dependencies import get_session


class FakeSession:
    def __init__(self) -> None:
        self.committed = False
        self.rolled_back = False
        self.closed = False

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True


class FakeSessionContext:
    def __init__(self, session: FakeSession) -> None:
        self.session = session

    async def __aenter__(self) -> AsyncSession:
        return cast(AsyncSession, self.session)

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object,
    ) -> None:
        del exception_type, exception, traceback
        self.session.closed = True


class FakeSessionFactory:
    def __init__(self, session: FakeSession) -> None:
        self.session = session

    def __call__(self) -> FakeSessionContext:
        return FakeSessionContext(self.session)


def create_request(session: FakeSession) -> Request:
    app = FastAPI()
    app.state.session_factory = cast(
        async_sessionmaker[AsyncSession], FakeSessionFactory(session)
    )
    return Request({"type": "http", "app": app})


@pytest.mark.asyncio
async def test_session_dependency_commits_and_closes() -> None:
    fake_session = FakeSession()
    dependency = cast(
        AsyncGenerator[AsyncSession, None], get_session(create_request(fake_session))
    )

    yielded_session = await anext(dependency)
    assert yielded_session is cast(AsyncSession, fake_session)
    with pytest.raises(StopAsyncIteration):
        await anext(dependency)

    assert fake_session.committed is True
    assert fake_session.rolled_back is False
    assert fake_session.closed is True


@pytest.mark.asyncio
async def test_session_dependency_rolls_back_and_closes() -> None:
    fake_session = FakeSession()
    dependency = cast(
        AsyncGenerator[AsyncSession, None], get_session(create_request(fake_session))
    )
    await anext(dependency)

    with pytest.raises(RuntimeError, match="request failed"):
        await dependency.athrow(RuntimeError("request failed"))

    assert fake_session.committed is False
    assert fake_session.rolled_back is True
    assert fake_session.closed is True
