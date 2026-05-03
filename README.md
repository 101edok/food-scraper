# lenta-scraper

Асинхронный скрапер каталога [lenta.com](https://lenta.com/) → MySQL (Yandex Cloud).

Подход: реверс внутреннего API через `httpx`. Категории читаются из SSR-данных
страницы (`<script id="ng-state">`), товары и карточки — через
`api-gateway/v1/catalog/items` и `…/catalog/items/<id>`. Идентификаторы магазина
и заголовки запроса определяются однократным recon на Playwright.

## Установка

```bash
# Любой современный менеджер: uv / poetry / pip
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[recon,dev]"
playwright install chromium     # только для recon
cp .env.example .env            # заполнить MYSQL_DSN
```

## Recon (выполняется один раз для города)

```bash
lenta-scrape recon --city moscow
# → recon/endpoints.json (заголовки, body-шаблоны, store_id)
# → recon/har.json       (полный сетевой трафик для отладки)
```

После recon при необходимости подправь `.env` (`DELIVERY_MODE`, и пр.).

## Локальный MySQL для smoke-теста

```bash
docker run --name lenta-mysql -p 3306:3306 \
  -e MYSQL_ROOT_PASSWORD=root \
  -e MYSQL_DATABASE=lenta \
  -e MYSQL_USER=lenta -e MYSQL_PASSWORD=lenta \
  -d mysql:8

lenta-scrape init-db
```

## Прогон

```bash
# smoke: одна категория, ограничение
lenta-scrape run --category-id 3009 --limit 50

# полный
lenta-scrape run

# отчёт
lenta-scrape stats
```

## Yandex Cloud MySQL

1. Скачай [`root.crt`](https://yandex.cloud/ru/docs/managed-mysql/operations/connect#get-ssl-cert) в `~/.mysql/root.crt`.
2. В `.env` поставь DSN:
   ```
   MYSQL_DSN=mysql+asyncmy://USER:PASS@HOST:3306/DB?ssl_ca=/Users/you/.mysql/root.crt
   ```
3. `lenta-scrape init-db && lenta-scrape run`.

## Структура

```
src/lenta_scraper/
  config.py           pydantic-settings из .env
  http_client.py      httpx + tenacity + aiolimiter
  api.py              типизированные обёртки над эндпоинтами
  models.py           SQLAlchemy ORM
  db.py               async engine/session
  upsert.py           идемпотентный INSERT … ON DUPLICATE KEY UPDATE
  recon.py            Playwright-разведка эндпоинтов
  pipeline/
    categories.py     обход дерева категорий
    listings.py       пагинация по категории
    details.py        карточки товаров
  cli.py              typer CLI
alembic/              миграции
```
