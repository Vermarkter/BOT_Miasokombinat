# Systemd Setup

## 1. Prepare app folder
- Copy the project to `/opt/BOT_Miasokombinat`.
- Create and fill `/opt/BOT_Miasokombinat/.env`.
- Create linux user:

```bash
sudo useradd --system --create-home --shell /usr/sbin/nologin meatbot
sudo chown -R meatbot:meatbot /opt/BOT_Miasokombinat
```

## 2. Install bot auto-start service

```bash
sudo cp deploy/systemd/meatbot.service /etc/systemd/system/meatbot.service
sudo systemctl daemon-reload
sudo systemctl enable --now meatbot.service
sudo systemctl status meatbot.service
```

## 3. Install daily backup timer

```bash
sudo cp deploy/systemd/meatbot-backup.service /etc/systemd/system/meatbot-backup.service
sudo cp deploy/systemd/meatbot-backup.timer /etc/systemd/system/meatbot-backup.timer
sudo systemctl daemon-reload
sudo systemctl enable --now meatbot-backup.timer
sudo systemctl list-timers meatbot-backup.timer
```

## 4. Manual backup test

```bash
sudo systemctl start meatbot-backup.service
ls -lah /opt/BOT_Miasokombinat/backups
```
