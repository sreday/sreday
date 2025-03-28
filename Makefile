years := $(wildcard 20*)

generate: $(years) static/index.html
	@echo ">>>" ${years}

20%: static/20%/index.html
	@echo ">>>" $@

.PRECIOUS: static/20%/index.html # don't mark as intermediary
static/20%/index.html: 20%/**/*
	$(eval LOCATION := 20$*)
	cd ${LOCATION} && \
		make clean && \
		make generate && \
		cp -r static/ ../static/${LOCATION}

static/index.html: home/**/*
	cd home && \
		make clean && \
		make generate && \
		cp -r static/* ../static
	cp -r photos ./static
	cp -r speakers ./static

env:
	python3 -m venv env

deps:
	pip install -r _build/requirements.txt
	mkdir -p static

clean:
	rm -rf ./static/*

serve:
	python3 _build/serve.py

.PHONY: env deps clean serve
