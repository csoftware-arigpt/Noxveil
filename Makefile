# Makefile for Noxveil

# Default target
.PHONY: help
help:
	@echo "Noxveil - Available Commands"
	@echo ""
	@echo "  make install          Install dependencies"
	@echo "  make run              Start server (local mode)"
	@echo "  make run-tunnel       Start server with tunnel"
	@echo "  make docker-build     Build Docker image"
	@echo "  make docker-run       Run Docker container"
	@echo "  make docker-down      Stop Docker container"
	@echo "  make clean            Clean up data files"
	@echo "  make agent-build      Build agent payload"
	@echo ""

# Install dependencies
.PHONY: install
install:
	cd server && pip install -r requirements.txt

# Start server locally (no tunnel)
.PHONY: run
run:
	cd server && python main.py

# Start server with tunnel
.PHONY: run-tunnel
run-tunnel:
	./start.sh

# Docker build
.PHONY: docker-build
docker-build:
	docker-compose build

# Docker run
.PHONY: docker-run
docker-run:
	docker-compose up -d

# Docker stop
.PHONY: docker-down
docker-down:
	docker-compose down

# Clean data directory
.PHONY: clean
clean:
	rm -rf data/*.db data/*.txt
	rm -rf __pycache__ server/__pycache__ agent/__pycache__
	rm -rf .pytest_cache

# Build agent payload
.PHONY: agent-build
agent-build:
	python agent/agent_builder.py --output /tmp/agent.py

# Full rebuild with Docker
.PHONY: rebuild
rebuild: docker-down docker-build docker-run
