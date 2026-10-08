.PHONY: install install-dev run test lint typecheck security smoke ci

install:
	pip install -r requirements.txt
install-dev:
	pip install -r requirements-dev.txt
run:
	uvicorn urlshort.main:app_factory --factory --reload --port 8000 --no-access-log --log-config config/uvicorn-logging.json
test:
	pytest
lint:
	ruff check urlshort sdlc tests
typecheck:
	mypy
security:
	bandit -q -r urlshort
	bandit -q -r sdlc -ll
	pip-audit -r requirements.txt --progress-spinner off
smoke:
	./scripts/smoke_test.sh http://localhost:8000
# Same gates as CI (except the Docker integration job)
ci: lint typecheck security
	pytest --cov=urlshort --cov=sdlc --cov-branch
	coverage report --include="urlshort/*" --fail-under=100
	coverage report --include="sdlc/*" --fail-under=100
