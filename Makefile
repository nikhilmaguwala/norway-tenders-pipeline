.PHONY: test build analyse offline

test:
	pytest -q

offline:
	python -m norway_tenders.cli build --offline

analyse:
	python -m norway_tenders.cli analyse

build: offline analyse
