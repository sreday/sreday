generate: 2023

2023:
	cd 2023-london && \
		make clean && \
		make generate && \
		cp -r static ../static/2023-london

env:
	python3 -m venv env

deps:
	pip install -r _build/requirements.txt
	mkdir -p static

clean:
	rm -rf ./static/*

serve:
	python _build/serve.py

.PHONY: generate 2023 env deps clean serve
