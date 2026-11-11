.PHONY: test check render smoke-local

test:
	python3 -m unittest discover -s tests

check:
	scripts/check.py docker-compose.yml
	scripts/check.py docker-compose.backups.yml

render:
	scripts/render.py

smoke-local:
	tests/local/run.sh
