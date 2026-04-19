# Systemd Setup

## 1. Prepare app folder and user

```bash
sudo useradd --system --create-home --shell /usr/sbin/nologin meatbot
sudo mkdir -p /opt/BOT_Miasokombinat
sudo chown -R meatbot:meatbot /opt/BOT_Miasokombinat
```

## 2. Upload code and install dependencies

```bash
# Run as meatbot user or via sudo -u meatbot
cd /opt/BOT_Miasokombinat
# Upload project files here (git clone or rsync)

python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

## 3. Configure `.env`

```bash
cd /opt/BOT_Miasokombinat
cp .env.example .env
# Fill real values:
# BOT_TOKEN, ONE_C_BASE_URL, ONE_C_USERNAME, ONE_C_PASSWORD, ADMIN_IDS
```

## 4. Finalize permissions

```bash
sudo chown -R meatbot:meatbot /opt/BOT_Miasokombinat
```

## 5. Install bot auto-start service

```bash
sudo cp deploy/systemd/meatbot.service /etc/systemd/system/meatbot.service
sudo systemctl daemon-reload
sudo systemctl enable --now meatbot.service
sudo systemctl status meatbot.service
```

## 6. Install daily backup timer

```bash
sudo cp deploy/systemd/meatbot-backup.service /etc/systemd/system/meatbot-backup.service
sudo cp deploy/systemd/meatbot-backup.timer /etc/systemd/system/meatbot-backup.timer
sudo systemctl daemon-reload
sudo systemctl enable --now meatbot-backup.timer
sudo systemctl list-timers meatbot-backup.timer
```

## 7. Manual backup test

```bash
sudo systemctl start meatbot-backup.service
ls -lah /opt/BOT_Miasokombinat/backups
```

## 8. 1C diagnostics log

```bash
tail -f /opt/BOT_Miasokombinat/logs/one_c_http_requests.log
```
