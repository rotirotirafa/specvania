"""Exercise the API in process, without a running server or network requests."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from todo_api.app import create_app


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as test_client:
        yield test_client


def test_create_and_get_todo(client: TestClient) -> None:
    response = client.post("/todos", json={"title": "  Buy milk  "})
    assert response.status_code == 201
    assert response.json() == {"id": 1, "title": "Buy milk", "completed": False}
    fetched = client.get("/todos/1")
    assert fetched.status_code == 200
    assert fetched.json() == response.json()


def test_list_todos_in_creation_order(client: TestClient) -> None:
    empty = client.get("/todos")
    assert empty.status_code == 200
    assert empty.json() == []
    first = client.post("/todos", json={"title": "First"}).json()
    second = client.post("/todos", json={"title": "Second"}).json()
    response = client.get("/todos")
    assert response.status_code == 200
    assert response.json() == [first, second]


def test_complete_is_persisted_and_idempotent(client: TestClient) -> None:
    client.post("/todos", json={"title": "Finish sample"})
    response = client.patch("/todos/1/complete")
    expected = {"id": 1, "title": "Finish sample", "completed": True}
    assert response.status_code == 200
    assert response.json() == expected
    repeated = client.patch("/todos/1/complete")
    assert repeated.status_code == 200
    assert repeated.json() == expected
    assert client.get("/todos/1").json() == expected
    assert client.get("/todos").json() == [expected]


def test_delete_removes_todo_and_does_not_reuse_id(client: TestClient) -> None:
    client.post("/todos", json={"title": "Remove me"})
    response = client.delete("/todos/1")
    assert response.status_code == 204
    assert response.content == b""
    assert client.get("/todos/1").status_code == 404
    assert client.get("/todos").json() == []
    assert client.post("/todos", json={"title": "Keep me"}).json()["id"] == 2


@pytest.mark.parametrize("method", ["get", "patch", "delete"])
def test_missing_todo_returns_404(client: TestClient, method: str) -> None:
    path = "/todos/999/complete" if method == "patch" else "/todos/999"
    response = client.request(method, path)
    assert response.status_code == 404
    assert response.json() == {"detail": "Todo not found"}


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"title": ""},
        {"title": "   "},
        {"title": "x" * 201},
        {"title": None},
        {"title": 123},
        {"title": "Valid", "completed": True},
    ],
)
def test_invalid_create_does_not_change_store(
    client: TestClient, payload: dict[str, object]
) -> None:
    assert client.post("/todos", json=payload).status_code == 422
    assert client.get("/todos").json() == []
    assert client.post("/todos", json={"title": "Valid"}).json()["id"] == 1


def test_each_app_has_an_independent_store(client: TestClient) -> None:
    client.post("/todos", json={"title": "Only in first app"})
    with TestClient(create_app()) as other_client:
        assert other_client.get("/todos").json() == []
        response = other_client.post("/todos", json={"title": "Second app"})
        assert response.json()["id"] == 1
    assert len(client.get("/todos").json()) == 1
