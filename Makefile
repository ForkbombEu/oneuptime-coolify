.PHONY: test check render smoke-local try try-down

test:
	python3 -m unittest discover -s tests -t .

check:
	scripts/check.py docker-compose.yml
	scripts/check.py docker-compose.backups.yml

render:
	scripts/render.py

smoke-local:
	tests/local/run.sh

try:
	tests/local/try.sh up

try-down:
	tests/local/try.sh down
