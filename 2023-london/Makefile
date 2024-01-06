generate: theme
	python _build/generate.py

deps:
	pip install -r _build/requirements.txt
	mkdir -p static

env:
	python3 -m venv env

clean:
	rm -rf ./static/*

theme:
	cp -r template/assets static/assets
	cp -r template/favicon.ico static
	cp -r assets static

serve:
	python _build/serve.py

.PHONY: deps generate clean serve theme
