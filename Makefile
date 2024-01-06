generate: 2023 home

2023:
	cd 2023-london && \
		make clean && \
		make generate && \
		cp -r static ../static/2023-london

home:
	cd home && \
		make clean && \
		make generate && \
		cp -r static ../static

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
