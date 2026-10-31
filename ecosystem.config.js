// pm2 process definitions for russia-03, next to recepter-backend / recepter-admin
// (repo checked out at ~/food-scraper, own .venv at ~/food-scraper/.venv).
// Start/adopt: pm2 start ecosystem.config.js && pm2 save
//
// Один процесс = один магазин × один город. Прогон разовый: pm2 запускает его
// при start и дальше по cron_restart; autorestart выключен, иначе pm2
// перезапускал бы скрейпер сразу после выхода. Время cron — локальное время сервера.
// Новый город: добавить строку в TARGETS (slug из src/food_scraper/cities.py),
// сдвинув время, чтобы прогоны не шли одновременно.
const TARGETS = [
  { shop: "magnit", city: "moscow", cron: "0 4 * * *" },
];

module.exports = {
  apps: TARGETS.map(({ shop, city, cron }) => ({
    name: `food-scraper-${shop}-${city}`,
    cwd: "/home/gistrec/food-scraper",
    script: "/home/gistrec/food-scraper/.venv/bin/food-scrape",
    args: `run --shop ${shop} --city ${city}`,
    interpreter: "none",
    autorestart: false,
    cron_restart: cron,
  })),
};
