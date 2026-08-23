.PHONY: lint typecheck test fmt

lint:
	ruff check .

typecheck:
	mypy src

test:
	pytest -m "not integration"

fmt:
	ruff format .
	ruff check --fix .
