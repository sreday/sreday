generate: 2022 2023 2024 2025 landing

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
	cd 2024-amsterdam && \
		make clean && \
		make generate && \
		cp -vr static/ ../static/2024-amsterdam
	cd 2024-san-francisco && \
		make clean && \
		make generate && \
		cp -vr static/ ../static/2024-san-francisco
	cd 2024-san-francisco-q4 && \
		make clean && \
		make generate && \
		cp -vr static/ ../static/2024-san-francisco-q4

2025:
	cd 2025-nyc-q1 && \
		make clean && \
		make generate && \
		cp -vr static/ ../static/2025-nyc-q1
	cd 2025-london-q1 && \
		make clean && \
		make generate && \
		cp -vr static/ ../static/2025-london-q1
	cd 2025-san-francisco-q2 && \
		make clean && \
		make generate && \
		cp -vr static/ ../static/2025-san-francisco-q2

landing:
	cd home && \
		make clean && \
		make generate && \
		cp -vr static/* ../static
	cp -vr photos ./static
	cp -vr speakers ./static

env:
	python3 -m venv env

deps:
	pip install -r _build/requirements.txt
	mkdir -p static

clean:
	rm -rf ./static/*

serve:
	python3 _build/serve.py

.PHONY: generate 2022 2023 2024 landing env deps clean serve
