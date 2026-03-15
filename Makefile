DOCKER_COMPOSE = docker-compose
COMPOSE_FILE = docker-compose.yml
BOT = bot

up:
	$(DOCKER_COMPOSE) -f $(COMPOSE_FILE) up -d

down:
	$(DOCKER_COMPOSE) -f $(COMPOSE_FILE) down

build:
	$(DOCKER_COMPOSE) -f $(COMPOSE_FILE) up -d --build

debug:
	$(MANAGE) debug

piplock:
	pipenv install
	sudo chown -R ${USER} Pipfile.lock

lint:
	isort .
	flake8
	black --config pyproject.toml .

check_lint:
	poetry run isort --check-only .
	poetry run flake8
	poetry run black --check --config pyproject.toml .

test:
	poetry run pytest -v --cov=$(BOT)