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

## Обход Qrator: импорт сессии браузера

Лента сидит за Qrator + DataDome. Headless-Chromium и httpx они режут по
TLS/JA-фингерпринту, но твой обычный браузер уже один раз прошёл challenge,
и `qrator_jsid` живёт долго. Самый простой обход — пересадить сессию в скрапер.

1. Открой `https://<город>.lenta.com/catalog/` в обычном браузере (Chrome/Yandex Browser).
2. DevTools → Network → правый клик на запросе к `lenta.com` → **Copy → Copy as cURL (bash)**.
3. Передай в скрапер:

```bash
pbpaste | lenta-scrape import-session                  # macOS
xclip -o -selection clipboard | lenta-scrape import-session   # Linux X11
lenta-scrape import-session -f request.curl            # из файла
```

Сохранится `recon/browser_session.json`. При следующем `lenta-scrape run`
httpx подхватит cookies + headers и пойдёт как браузер. Playwright не нужен.

Сессия живёт несколько часов. Если в логах увидишь `browser_session_expired`
— открой страницу в браузере заново и повтори импорт.

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

## Прокси через VM в Yandex Cloud

Если запускаешь скрапер не из РФ, Qrator (антибот Ленты) выдаёт 403 даже на
headless-Chromium. Решение — пустить трафик через российскую VM. **Самый
простой путь — SSH SOCKS5-туннель**, ничего на VM ставить не надо:

```bash
ssh -D 1080 -N -f your-user@<vm-ip>     # фоновый туннель
```

В `.env`:
```
PROXY_URL=socks5://127.0.0.1:1080
```

И `httpx`, и Playwright (qrator warm-up) уйдут через RU-IP.

Альтернатива — `tinyproxy` на VM (HTTP CONNECT-прокси):
```bash
sudo apt install -y tinyproxy
sudo tee /etc/tinyproxy/tinyproxy.conf >/dev/null <<EOF
Port 8888
Listen 0.0.0.0
Allow <твой-IP>/32
EOF
sudo systemctl restart tinyproxy
# открыть 8888 в Security Group YC только для своего IP
```
В `.env`:
```
PROXY_URL=http://<vm-ip>:8888
```

> **Не используй nginx как forward-proxy для HTTPS** — стандартная сборка
> не поддерживает CONNECT, нужен пересобранный с `ngx_http_proxy_connect_module`.
> tinyproxy / 3proxy / SSH-туннель проще и надёжнее.

## Yandex Cloud MySQL

1. Скачай [`root.crt`](https://yandex.cloud/ru/docs/managed-mysql/operations/connect#get-ssl-cert) в `~/.mysql/root.crt`.
2. В `.env` поставь DSN:
   ```
   MYSQL_DSN=mysql+asyncmy://USER:PASS@HOST:3306/DB?ssl_ca=/Users/you/.mysql/root.crt
   ```
3. `lenta-scrape init-db && lenta-scrape run`.

## Деплой на russia-03

Скрейпер живёт рядом с edalle backend: отдельный checkout в `~/food-scraper`,
свой `.venv`, свой pm2-процесс `lenta-scraper` (`ecosystem.config.js`).
Сервер в РФ, поэтому `PROXY_URL` там пустой, а Qrator проходится headless-прогревом
Playwright — `recon/browser_session.json` на сервер не кладём.

Первый раз:

```bash
ssh russia-03
git clone git@github.com:101edok/food-scraper.git ~/food-scraper && cd ~/food-scraper
python3 -m venv .venv && .venv/bin/pip install -e ".[recon]"   # на сервере Python 3.12
.venv/bin/playwright install --with-deps chromium
cp .env.example .env            # MYSQL_DSN=…@public.mysql.gistrec.cloud…, PROXY_URL пустой
.venv/bin/lenta-scrape db-check
.venv/bin/lenta-scrape run --category-id 3009 --limit 50   # smoke
pm2 start ecosystem.config.js && pm2 save
```

Обновление:

```bash
ssh russia-03 'cd food-scraper && git pull --ff-only && .venv/bin/pip install -e ".[recon]"'
ssh russia-03 'pm2 restart lenta-scraper'   # внимание: restart сразу запускает прогон
```

Прогон каждый день в 04:00 по времени сервера. Логи: `pm2 logs lenta-scraper`,
итоги: `lenta-scrape stats`.

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
