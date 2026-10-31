# Магнит (magnit.ru) — разведка 2026-10-31

Антибота нет: с russia-03 (дата-центр) API отвечает 200, 30 запросов при
10 параллельных — без 429. Playwright не нужен, хватает httpx.

## База и заголовки

`https://magnit.ru/webgate` — BFF фронта (Nuxt). Достаточно:

```
Content-Type: application/json
Accept: application/json
X-Device-Platform: Web
X-New-Magnit: true
X-Device-ID: <любой UUID, фиксированный>
X-Client-Name: magnit
Origin: https://magnit.ru
Referer: https://magnit.ru/catalog/
```

## Магазин

Цены и ассортимент привязаны к магазину. Без адреса фронт берёт Краснодар
(`992301`, `storetype=dostavka`, `catalogtype=3`).

Магазин по точке: `GET /shops/v1/shop_by_point?x=<широта>&y=<долгота>` →
`shops[]` с `delivery_type` (`express` — «Магнит у дома», `cosmetic`, …),
`store_type` и `xml_id` (= `storeCode`). В точке у Кремля доставки нет — пустой
список, поэтому адаптер пробует кольцо точек вокруг центра.

Пара `catalogType`/`storeType` проверяется сервером (`422 invalid_service_pair`).
Для `express` (store_type `1`) работают:

| catalogType / storeType | Цены |
|---|---|
| `1` / `1` | на полке — **берём эти** |
| `3` / `express` | на полке (совпадают с 1/1) |
| `2` / `express` | доставки, дороже (коктейль Neo: 169,99 против 129,99) |

`POST /v1/stores-facade/search` тело так и не подобрано (всегда
`query or geo_bounding_box or circle or favorites or cityFIASID is required`).

## Эндпоинты

| Что | Запрос |
|---|---|
| Дерево категорий | `GET /v3/categories/store/{storeCode}?storetype=…&catalogtype=…` → `{items:[{id,name,code,image,children}]}`; 676 узлов, 555 листьев. Корни — смесь товарных разделов и промо-подборок («Только у нас», «Акции каждый день»), товары в них дублируются — пайплайн сохраняет товар один раз за прогон, а настоящую категорию берёт из карточки |
| Листинг | `POST /v2/goods/search` body `{catalogType, storeType, storeCode, categories:[id], sort:{order:"desc",type:"popularity"}, pagination:{limit, offset}}` → `{items, pagination:{totalCount, hasMore, nextOffset}}`. Брать `offset = nextOffset`: при limit=50 приходит 45 позиций, а `nextOffset=55` |
| Карточка | `GET /v2/goods/{id}/stores/{storeCode}?storetype=…&catalogtype=…` |

## Поля

Листинг: `id`, `name`, `price` (**копейки**, int), `promotion.oldPrice`,
`promotion.discountPercent`, `quantity` (остаток), `gallery[].url`,
`seoCode` (URL: `/product/{id}-{seoCode}`), `weighted` (весовой товар:
`unitPrice`, `unitLabel`), `isForAdults`, `ratings`.

Карточка дополнительно: `categories` (id листа), `details[]` — группы по `type`:
`nutritionFactsType` (Ккал/Белки/Жиры/Углеводы на 100 г), `stringType`
«Состав», `tableType` «Характеристики» (Бренд, Производитель, Вес, Артикул),
«Условия хранения».

SSR-страница `/catalog/` тоже содержит первую страницу товаров (Nuxt
`data-nuxt-data`, формат devalue) и JSON-LD `OfferCatalog` — запасной путь,
если webgate закроют.
