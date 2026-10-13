"""An app factory with an in-memory store owned by each app instance."""

from threading import Lock

from fastapi import FastAPI, HTTPException, Response, status

from todo_api.models import Todo, TodoCreate


class TodoStore:
    """Protect sync request handlers with a lock; ids are never reused."""

    def __init__(self) -> None:
        self._todos: dict[int, Todo] = {}
        self._next_id = 1
        self._lock = Lock()

    def create(self, payload: TodoCreate) -> Todo:
        with self._lock:
            todo = Todo(id=self._next_id, title=payload.title)
            self._todos[todo.id] = todo
            self._next_id += 1
            return todo

    def list(self) -> list[Todo]:
        with self._lock:
            return list(self._todos.values())

    def get(self, todo_id: int) -> Todo:
        with self._lock:
            return self._require(todo_id)

    def complete(self, todo_id: int) -> Todo:
        with self._lock:
            todo = self._require(todo_id).model_copy(update={"completed": True})
            self._todos[todo_id] = todo
            return todo

    def delete(self, todo_id: int) -> None:
        with self._lock:
            self._require(todo_id)
            del self._todos[todo_id]

    def _require(self, todo_id: int) -> Todo:
        todo = self._todos.get(todo_id)
        if todo is None:
            raise HTTPException(status_code=404, detail="Todo not found")
        return todo


def create_app() -> FastAPI:
    app = FastAPI(title="Sample Todo API")
    store = TodoStore()

    @app.post("/todos", response_model=Todo, status_code=status.HTTP_201_CREATED)
    def create_todo(payload: TodoCreate) -> Todo:
        return store.create(payload)

    @app.get("/todos", response_model=list[Todo])
    def list_todos() -> list[Todo]:
        return store.list()

    @app.get("/todos/{todo_id}", response_model=Todo)
    def get_todo(todo_id: int) -> Todo:
        return store.get(todo_id)

    @app.patch("/todos/{todo_id}/complete", response_model=Todo)
    def complete_todo(todo_id: int) -> Todo:
        return store.complete(todo_id)

    @app.delete("/todos/{todo_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_todo(todo_id: int) -> Response:
        store.delete(todo_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return app
