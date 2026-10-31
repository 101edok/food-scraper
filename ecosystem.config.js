// pm2 process definitions for russia-03, next to recepter-backend / recepter-admin
// (repo checked out at ~/food-scraper, own .venv at ~/food-scraper/.venv).
// Start/adopt: pm2 start ecosystem.config.js && pm2 save
//
// На каждую сеть × город два процесса:
//   food-scraper-<shop>-<city>       каждый день, 12 точек, разнесённых по районам (одни и те же);
//   food-scraper-<shop>-<city>-full  раз в неделю: обновить справочник точек и обойти все.
// Прогон разовый: pm2 запускает его при start и дальше по cron_restart; autorestart
// выключен, иначе pm2 перезапускал бы скрейпер сразу после выхода. Время cron — время
// сервера (Europe/Moscow). Полный прогон Москвы ~5 ч, поэтому в воскресенье ежедневного нет.
// Новый город: строка в TARGETS (slug из src/food_scraper/cities.py) со сдвинутым временем.
const TARGETS = [
  { shop: "magnit", city: "moscow", daily: "0 4 * * 1-6", weekly: "0 1 * * 0" },
  // Дикси за Qrator: 2 запроса/с, 2 потока, 6 точек в день. Два прогона одновременно (6 rps и частая
  // смена точки) 2026-10-31 получили бан IP russia-03 — ежедневный и полный не пересекаются.
  { shop: "dixy", city: "moscow", daily: "30 5 * * 1-6", weekly: "0 7 * * 0", stores: 6, workers: 2 },
  // Fix Price: цена по городу — одна точка, ~1 мин; полный прогон только обновляет справочник
  { shop: "fixprice", city: "moscow", daily: "0 6 * * 1-6", weekly: "0 6 * * 0" },
  // ВкусВилл через официальный MCP: 1 запрос/с, ~300 поисковых запросов (ингредиенты рецептов + базовые
  // продукты) по 3 страницы — ~15 мин; полный прогон обновляет справочник точек (~3 мин)
  { shop: "vkusvill", city: "moscow", daily: "30 6 * * 1-6", weekly: "30 6 * * 0", workers: 1 },
];

const app = (name, args, cron) => ({
  name,
  cwd: "/home/gistrec/food-scraper",
  script: "/home/gistrec/food-scraper/.venv/bin/food-scrape",
  args,
  interpreter: "none",
  autorestart: false,
  cron_restart: cron,
});

module.exports = {
  apps: TARGETS.flatMap(({ shop, city, daily, weekly, stores = 12, workers = 4 }) => [
    app(`food-scraper-${shop}-${city}`, `run --shop ${shop} --city ${city} --stores ${stores} --workers ${workers}`, daily),
    app(`food-scraper-${shop}-${city}-full`, `run --shop ${shop} --city ${city} --all-stores --refresh-stores --workers ${workers}`, weekly),
  ]),
};
