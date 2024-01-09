generate: 2022 2023 2024 landing

2022:
	cd 2022-london && \
		make clean && \
		make generate && \
		cp -r static/ ../static/2022-london

2023:
	cd 2023-london && \
		make clean && \
		make generate && \
		cp -vr static/ ../static/2023-london

2024:
	cd 2024-london && \
		make clean && \
		make generate && \
		cp -vr static/ ../static/2024-london

landing:
	cd home && \
		make clean && \
		make generate && \
		cp -vr static/* ../static

env:
	python3 -m venv env

deps:
	pip install -r _build/requirements.txt
	mkdir -p static

clean:
	rm -rf ./static/*

serve:
	python _build/serve.py

.PHONY: generate 2022 2023 2024 landing env deps clean serve
