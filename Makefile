.PHONY: lint test build check install

UV ?= uv
INSTALL_BIN ?= $(shell $(UV) tool dir --bin)

lint:
	$(UV) run ruff check .

test:
	$(UV) run pytest

build:
	$(UV) build

check: lint test build

install:
	UV_TOOL_BIN_DIR="$(INSTALL_BIN)" $(UV) tool install --force --reinstall .
