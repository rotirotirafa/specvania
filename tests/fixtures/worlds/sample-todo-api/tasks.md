## A1 · Due dates on todos
boss: S1

### T01 · Add optional validated due date to models and creation
depends_on: none
covers: S1
files: todo_api/models.py, todo_api/app.py, tests/test_todos.py

Checklist:
- [ ] Creating a todo with a valid `YYYY-MM-DD` due date returns 201 and echoes the date in the response body
- [ ] Creating a todo without a due date returns 201 with `due_date` null, and the date is visible in GET responses
- [ ] Creating a todo with a malformed due date returns 422
- [ ] Creating a todo with a past due date is accepted (201)
- [ ] Completion and deletion behavior, including 404s, is unchanged for todos with due dates

## A2 · Overdue filter on listing
boss: S2

### T02 · Implement GET /todos?overdue=true filtering
depends_on: T01
covers: S2
files: todo_api/app.py, tests/test_todos.py

Checklist:
- [ ] `GET /todos?overdue=true` returns only incomplete todos with due dates before today's server date
- [ ] Overdue results exclude completed todos and todos with no due date or a due date of today or later
- [ ] Overdue results preserve creation order
- [ ] An invalid `overdue` query value returns 422

## A3 · Unfiltered listing integrity
boss: S3

### T03 · Preserve unfiltered listing behavior with due dates
depends_on: T02
covers: S3
files: todo_api/app.py, tests/test_todos.py

Checklist:
- [ ] Omitting `overdue` returns all todos in creation order regardless of due date or completion state
- [ ] `overdue=false` returns all todos in creation order, same as omitting the parameter
- [ ] Unfiltered responses include the `due_date` field for every todo
