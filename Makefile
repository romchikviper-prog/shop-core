SHELL := /bin/sh

UV ?= uv
GIT_SHA ?= $(shell git rev-parse --short=7 HEAD 2>/dev/null || echo dev)
IMAGE ?= shop-core
TAG ?= $(GIT_SHA)
ENV ?= dev

.PHONY: help prepare build test lint format coverage complexity manifests image image-single scan ci deploy rollout-status fail undo smoke clean

help:
	@echo "make build        — wheel із вшитим хешем коміту"
	@echo "make test         — усі тести"
	@echo "make lint         — ruff + mypy --strict + перевірка формату"
	@echo "make coverage     — тести з порогом покриття 80 %"
	@echo "make complexity   — цикломатична складність <= 10"
	@echo "make manifests    — kustomize + kubeconform + hadolint"
	@echo "make image        — multi-stage образ із тегом $(TAG)"
	@echo "make image-single — одноетапний образ для вимірювання"
	@echo "make scan         — Trivy, CRITICAL зупиняє збірку"
	@echo "make ci           — ті самі ворота, що в конвеєрі"
	@echo "make deploy ENV=dev|prod — kind + kustomize + rollout status"
	@echo "make fail         — демонстраційний збій: неіснуючий тег образу"
	@echo "make undo         — kubectl rollout undo і перевірка"

prepare:
	$(UV) run python scripts/write_build_info.py $(GIT_SHA)

build: prepare
	$(UV) build --out-dir dist
	@echo "artifact: dist/ (wheel із GIT_SHA=$(GIT_SHA))"

test:
	$(UV) run pytest -q

format:
	$(UV) run ruff format --check src/shop/api tests/test_api_service.py tests/test_api_config.py tests/test_api_server.py scripts/write_build_info.py scripts/check_complexity.py

lint: prepare format
	$(UV) run ruff check src tests scripts
	$(UV) run mypy --strict src

coverage:
	COVERAGE_CORE=sysmon $(UV) run coverage run -m pytest -q
	$(UV) run coverage report

complexity:
	$(UV) run python scripts/check_complexity.py --max 10

manifests:
	kustomize build deploy/k8s/overlays/dev | kubeconform -strict -summary
	kustomize build deploy/k8s/overlays/prod | kubeconform -strict -summary
	hadolint --failure-threshold warning Dockerfile Dockerfile.single

image:
	docker build --build-arg GIT_SHA=$(GIT_SHA) -t $(IMAGE):$(TAG) .

image-single:
	docker build -f Dockerfile.single --build-arg GIT_SHA=$(GIT_SHA) -t $(IMAGE):single-$(TAG) .

scan:
	trivy image --severity CRITICAL --exit-code 1 --ignore-unfixed $(IMAGE):$(TAG)

ci: lint coverage complexity manifests build image scan

deploy: image
	kind get clusters | grep -qx shop || kind create cluster --name shop --config deploy/kind-config.yaml
	kind load docker-image $(IMAGE):$(TAG) --name shop
	kustomize build deploy/k8s/overlays/$(ENV) | sed 's|shop-core:local|$(IMAGE):$(TAG)|g' | kubectl apply -f -
	kubectl -n shop rollout status deployment/shop-core --timeout=120s

rollout-status:
	kubectl -n shop rollout status deployment/shop-core --timeout=120s

fail:
	kubectl -n shop set image deployment/shop-core shop-core=$(IMAGE):missing-$(TAG)
	-kubectl -n shop rollout status deployment/shop-core --timeout=20s
	kubectl -n shop get pods -o wide

undo:
	kubectl -n shop rollout undo deployment/shop-core
	kubectl -n shop rollout status deployment/shop-core --timeout=120s
	kubectl -n shop get pods -o wide

smoke:
	kubectl -n shop exec deployment/shop-core -- python -c "import urllib.request; print(urllib.request.urlopen('http://shop-core/healthz', timeout=5).read().decode())"

clean:
	rm -rf dist .coverage htmlcov
