// pm2 process definition for russia-03, next to recepter-backend / recepter-admin
// (repo checked out at ~/food-scraper, own .venv at ~/food-scraper/.venv).
// Start/adopt: pm2 start ecosystem.config.js && pm2 save
//
// Прогон разовый: pm2 запускает его сразу при start и дальше по cron_restart.
// autorestart выключен, иначе pm2 перезапускал бы скрейпер сразу после выхода.
// Время cron — локальное время сервера.
module.exports = {
  apps: [
    {
      name: "lenta-scraper",
      cwd: "/home/gistrec/food-scraper",
      script: "/home/gistrec/food-scraper/.venv/bin/lenta-scrape",
      args: "run",
      interpreter: "none",
      autorestart: false,
      cron_restart: "0 4 * * *",
    },
  ],
};
