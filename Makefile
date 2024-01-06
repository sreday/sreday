generate: theme
	python build/generate.py

deps:
	pip install -r build/requirements.txt
	mkdir -p static

env:
	python3 -m virtualenv env

clean:
	rm -rf ./static/*

theme:
	cp -r template/assets static/assets
	cp -r template/favicon.ico static
	cp -r assets static

serve:
	python build/serve.py

.PHONY: deps generate clean serve theme
