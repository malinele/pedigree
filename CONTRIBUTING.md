# Contributing

- Open an issue before starting any large change.
- Run the gates before and after your changes:

  ```bash
  .venv/bin/python -m ruff check src tests
  .venv/bin/python -m mypy --strict src
  .venv/bin/python -m pytest tests/ -q
  ```

- Use conventional commit style: `<type>: <description>`, with types such as
  `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `perf`, `ci`.
- Contributions are accepted under the project license
  ([PolyForm Noncommercial 1.0.0](LICENSE)).
