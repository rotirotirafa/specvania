### S1 · Todos carry an optional validated due date

```gherkin
Given a running app
When a todo is created with a valid due date, without a due date, and with an invalid due date
Then the first two create successfully with the date returned or null, and the invalid date returns 422
```

### S2 · Overdue filter returns only incomplete todos due before today

```gherkin
Given todos whose due dates fall before today, today, after today, or are unset
When GET /todos?overdue=true is requested
Then only incomplete todos due before today are returned, in creation order
```

### S3 · Unfiltered listing remains unchanged

```gherkin
Given todos with assorted due dates and completion states
When GET /todos is requested without the overdue parameter
Then all todos are returned in creation order including their due dates
```

## Context

The sample todo API (`todo_api/`, FastAPI, in-memory store per app) currently has
no due dates or filters. This feature adds an optional due date to todos and an
overdue filter on the list endpoint, per the Clarify answers recorded below.

## Decisions

- Todos gain an optional `due_date` field, ISO calendar date (`YYYY-MM-DD`),
  validated by Pydantic as a `datetime.date`. It appears in create requests and
  in all todo responses (`null` when unset).
- A todo is overdue when it is not completed and its `due_date` is strictly
  before `date.today()` on the server. Todos without a due date are never overdue.
- `GET /todos?overdue=true` returns only overdue todos in creation order,
  excluding completed todos. `overdue=false` or an omitted parameter returns all
  todos unfiltered (existing behavior). Any other value for `overdue` returns 422.
- Due dates are set at creation only; there is no update path. Past dates are
  accepted.
- Existing endpoint semantics (201/404/204/422, ID allocation, trimming, unknown
  fields) are unchanged.

## Clarify log

| # | Question | Answer |
|---|---|---|
| C1 | Is a due date optional, and what type should it be? | optional calendar date (YYYY-MM-DD) |
| C2 | What boundary defines a todo as overdue? | due date before today (server local date) |
| C3 | Should completed todos appear in overdue results? | exclude completed todos |
| C4 | How is overdue filtering requested and ordered? | GET /todos?overdue=true, creation order |
| C5 | Can a due date be changed or removed after creation, and are past dates accepted? | set at creation only; past dates accepted |
