"""Request and response models for the todo API."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

type Title = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]


class TodoCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Title


class Todo(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    title: str
    completed: bool = False
