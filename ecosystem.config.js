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
  // Дикси: 3 запроса/с (Qrator), точка ~3 мин — полный прогон Москвы ~7 ч, после Магнита
  { shop: "dixy", city: "moscow", daily: "30 5 * * 1-6", weekly: "0 7 * * 0" },
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
  apps: TARGETS.flatMap(({ shop, city, daily, weekly }) => [
    app(`food-scraper-${shop}-${city}`, `run --shop ${shop} --city ${city} --stores 12`, daily),
    app(`food-scraper-${shop}-${city}-full`, `run --shop ${shop} --city ${city} --all-stores --refresh-stores`, weekly),
  ]),
};
