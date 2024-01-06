env:
	python3 -m venv env

assets:
	rm -rf static
	mkdir static

	cp -r template/assets static/assets
	cp -r template/favicon.ico static

	cp -r assets/ static/assets/

# source env/bin/activate
deps:
	pip install -r build/requirements.txt
	mkdir -p static

generate: assets
	python build/generate.py

serve:
	python build/serve.py


clean:
	rm -rf ./static/*


.PHONY: deps assets generate serve clean
