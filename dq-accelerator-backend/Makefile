.PHONY: install fixtures test run docker clean

install:
	pip install -r requirements-dev.txt

fixtures:
	python tools/make_test_data.py --out tests/fixtures

test: fixtures
	pytest

run:
	uvicorn dqa.api.app:app --reload --port 8000

docker:
	docker compose up --build

clean:
	rm -rf data .pytest_cache .coverage htmlcov
	find . -name __pycache__ -type d -exec rm -rf {} +
