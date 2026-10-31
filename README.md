# food-scraper

Асинхронный скрейпер каталогов продуктовых магазинов → MySQL. Таблицы `shop_*` лежат
в общей с edalle backend базе `recepter`.

| Магазин | Статус | Как |
|---|---|---|
| Магнит | работает | BFF фронта `magnit.ru/webgate`, антибота нет — см. [recon/magnit.md](recon/magnit.md) |
| Дикси | работает | PHP-ручки Битрикса `/ajax/*.php`, магазин в PHP-сессии, Qrator пропускает — см. [recon/dixy.md](recon/dixy.md) |
| Fix Price | работает, только «Продукты и напитки» | API фронта `api.fix-price.com/buyer/v1`; цена по городу, не по точке — одна точка на город — см. [recon/fixprice.md](recon/fixprice.md) |
| ВкусВилл | работает, по поиску | официальный MCP-сервер `mcp.vkusvill.ru/mcp`: поиск по ингредиентам рецептов edalle и базовым продуктам, цена одна на город (без OAuth) — см. [recon/vkusvill.md](recon/vkusvill.md) |
| Лента | нужен RU-резидентный прокси | реверс `api-gateway`, Qrator режет IP дата-центров (russia-03 и finland-01 — 403) |

Один прогон — одна сеть × один город, по нескольким точкам сети. Цена хранится по
точке (`store_code`) и всегда вместе с городом (`shop_prices`, `shop_price_snapshots`):
внутри одного города точки продают по разным ценам (у Магнита в Москве — у 15–26% общих
товаров), «цена города» считается по ним в админке (min / медиана). Точки — в справочнике
`shop_stores`.

## Установка

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[recon,dev]"
playwright install chromium     # только для Ленты
cp .env.example .env            # заполнить MYSQL_DSN
food-scrape init-db             # alembic upgrade head → таблицы shop_*
```

## Прогон

```bash
# справочник точек города: обход сеткой ~2 км (Москва — ~490 точек, ~2 мин)
food-scrape stores --shop magnit --city moscow

# smoke: 2 точки, одна категория, 20 товаров, 5 карточек
food-scrape run --shop magnit --city moscow --stores 2 --category-id 63983 --limit 20 --details-limit 5

# ежедневный: 12 точек, разнесённых по районам (каждый день одни и те же)
food-scrape run --shop magnit --city moscow --stores 12

# еженедельный: обновить справочник и обойти все точки
food-scrape run --shop magnit --city moscow --all-stores --refresh-stores

# отчёт
food-scrape stats
```

Что делает прогон:

1. Берёт активные точки города из `shop_stores`. Справочник пуст или `--refresh-stores` —
   обходит город сеткой (`cities.py`: центр и радиус), новые точки добавляет, пропавшие
   помечает неактивными (история цен по ним остаётся).
2. Выбирает точки: `--stores N` — N штук farthest-point выборкой от центра (детерминированно);
   `--all-stores` — все.
3. По каждой точке: дерево категорий, листовые категории — товары и цены точки. Цена пишется
   в `shop_prices`, а в `shop_price_snapshots` — только если цена или наличие изменились.
4. Дочитывает карточки (состав, КБЖУ, бренд, вес) один раз на сеть — только для новых
   товаров и тех, чья карточка старше `--details-max-age-days` (30).

Счётчики прогона (`shop_scrape_runs`) обновляются после каждой точки — в админке виден прогресс.

## Постобработка

`food-scrape postprocess` (pm2 `food-scraper-postprocess`, каждый день в 09:00) — три шага:

1. `match` — ингредиенты рецептов edalle (таблица `ingredients`) → товары сетей
   (`shop_ingredient_matches`, status `auto`). Все слова ингредиента (основы Snowball) есть
   в названии, и товар с одного из них начинается; «с/в/по» в названии — признак готового блюда.
   Решения модератора из админки (`approved`/`rejected`) не трогаются.
2. `recipe-costs` — стоимость рецептов (`shop_recipe_costs`) по сети и городу: количество из рецепта
   («200 г», «2 ст. л.», «2 зубчика») × цена ингредиента за g/ml/шт (медиана по точкам и товарам;
   approved важнее auto). Фасовка товара (`net_quantity`/`net_unit`) — из названия.
3. `check` — проверка данных (`shop_quality_issues`): данные старше 48 ч, нулевые цены, выбросы цены
   за единицу в категории, доля товаров без фасовки. Ошибки — в Telegram.

Алерты в Telegram: `ALERT_TELEGRAM_TOKEN`, `ALERT_TELEGRAM_CHAT_ID` в `.env`. Без них — только в лог.
Прогон шлёт алерт сам, если закончился блокировкой/ошибкой или товаров меньше половины обычного.

## Новый магазин

Адаптер в `src/food_scraper/shops/<name>.py` с контрактом `shops/base.py::Shop`:
`find_stores(lat, lon)`, `use_store(store)`, `categories()`, `iter_items(category_id)`,
`details(item_id)`.
Пайплайн, запись в БД и CLI общие. Зарегистрировать в `shops/base.py::get_shop`.

## Деплой на russia-03

Скрейпер живёт рядом с edalle backend: отдельный checkout в `~/food-scraper`,
свой `.venv`, по два pm2-процесса на сеть × город (`ecosystem.config.js`):
`food-scraper-magnit-moscow` — каждый день кроме воскресенья в 04:00, 12 точек;
`food-scraper-magnit-moscow-full` — в воскресенье в 01:00, справочник и все точки (~5 ч).

Первый раз:

```bash
ssh russia-03
git clone git@github.com:101edok/food-scraper.git ~/food-scraper && cd ~/food-scraper
python3 -m venv .venv && .venv/bin/pip install -e .    # на сервере Python 3.12
cp .env.example .env            # MYSQL_DSN → recepter, учётка backend
.venv/bin/food-scrape db-check && .venv/bin/food-scrape init-db
.venv/bin/food-scrape run --shop magnit --city moscow --stores 2 --category-id 63983 --limit 20   # smoke
pm2 start ecosystem.config.js && pm2 save
```

Обновление:

```bash
ssh russia-03 'cd food-scraper && git pull --ff-only && .venv/bin/pip install -e . && .venv/bin/food-scrape init-db'
ssh russia-03 'pm2 startOrReload ecosystem.config.js && pm2 save'   # restart сразу запускает прогон
```

Прогоны по cron из `ecosystem.config.js` (время сервера). Логи:
`pm2 logs food-scraper-magnit-moscow`, итоги: `food-scrape stats` или вкладка «Прогоны» в админке.

БД — через WireGuard (`10.10.0.4`, finland-01): публичный `public.mysql.gistrec.cloud` с
russia-03 теряет ~30% подключений по таймауту.

## Лента

Лента сидит за Qrator. С IP дата-центров `__qrator/validate` отвечает 403 даже
настоящему Chromium, так что на сервере нужен российский резидентный/мобильный
прокси в `PROXY_URL`. Локально можно пересадить сессию своего браузера:

1. Открой `https://<город>.lenta.com/catalog/` в обычном браузере.
2. DevTools → Network → правый клик на запросе к `lenta.com` → **Copy → Copy as cURL (bash)**.
3. `pbpaste | food-scrape import-session` — сохранится `recon/browser_session.json`
   (cookies, в git не попадает). Сессия живёт несколько часов: на
   `browser_session_expired` повтори импорт.

Город Ленты задаётся поддоменом в `.env` (`CITY_SUBDOMAIN`), разведка
эндпоинтов — `food-scrape recon --city moscow`, диагностика сети — `food-scrape probe`.

Прокси через свою VM: `ssh -D 1080 -N -f user@<vm-ip>` и `PROXY_URL=socks5://127.0.0.1:1080`
(или tinyproxy на VM; nginx как forward-proxy для HTTPS не подходит — нет CONNECT).

## Yandex Cloud MySQL

1. Скачай [`root.crt`](https://yandex.cloud/ru/docs/managed-mysql/operations/connect#get-ssl-cert) в `~/.mysql/root.crt`.
2. В `.env`:
   ```
   MYSQL_DSN=mysql+asyncmy://USER:PASS@public.mysql.gistrec.cloud:3306/recepter?ssl_ca=/Users/you/.mysql/root.crt&ssl_check_hostname=false
   ```

alembic ведёт свою таблицу версий `shop_alembic_version` и видит только таблицы
`shop_*` — таблицы backend он не трогает.

## Структура

```
src/food_scraper/
  config.py           pydantic-settings из .env
  cities.py           города: slug, центр, радиус обхода
  stores.py           сетка обхода, выборка точек
  models.py           SQLAlchemy ORM (shop_*)
  db.py               async engine/session
  upsert.py           идемпотентная запись (MySQL / SQLite в тестах)
  pipeline/run.py     прогон магазин × город
  shops/
    base.py           контракт адаптера + get_shop()
    magnit.py         Магнит
    dixy.py           Дикси
    fixprice.py       Fix Price
    vkusvill.py       ВкусВилл (MCP)
    lenta/            Лента: api, http_client, qrator, browser_session, recon
  cli.py              typer CLI
alembic/              миграции
recon/                заметки разведки
```
