# BOT_Miasokombinat (Windows Server 2022)

Короткий гайд запуску бота в `PowerShell` для Windows.

## 1) Підготовка середовища

```powershell
cd C:\path\to\BOT_Miasokombinat
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Заповніть `.env` реальними значеннями:
- `BOT_TOKEN`
- `ONE_C_BASE_URL`
- `ONE_C_USERNAME`
- `ONE_C_PASSWORD`
- `ONE_C_X_BOT_TOKEN`
- `ADMIN_IDS`

## 2) Ручний запуск (перевірка)

```powershell
.\venv\Scripts\Activate.ps1
python main.py
```

## 3) Запуск як Windows Service (NSSM, рекомендовано)

1. Завантажте `nssm.exe` і додайте в `PATH` (або передайте повний шлях).
2. Відкрийте `PowerShell` **від імені Адміністратора**.
3. Виконайте:

```powershell
.\deploy\windows\install_meatbot_service.ps1
```

Або з явним шляхом до `nssm.exe`:

```powershell
.\deploy\windows\install_meatbot_service.ps1 -NssmExe "C:\tools\nssm\nssm.exe"
```

Перевірка:

```powershell
Get-Service meatbot
```

## 4) Щоденний backup БД (Task Scheduler)

Відкрийте `PowerShell` **від імені Адміністратора**:

```powershell
.\deploy\windows\register_backup_task.ps1 -RunAt "02:00"
```

Перевірка:

```powershell
Get-ScheduledTask -TaskName MeatbotDailyBackup
```

## 5) Альтернатива без NSSM (автозавантаження через .bat)

Файл: `deploy\windows\run_meatbot.bat`

1. Натисніть `Win + R`
2. Введіть `shell:startup`
3. Створіть ярлик на `deploy\windows\run_meatbot.bat`

Це варіант для запуску від конкретного користувача після входу в систему.

## 6) Логи

- HTTP-запити до 1С: `logs\one_c_http_requests.log`
- stdout сервісу NSSM: `logs\service_stdout.log`
- stderr сервісу NSSM: `logs\service_stderr.log`

## 7) Примітка по шляхах

У коді використовується `pathlib` і відносні шляхи (`data`, `backups`, `logs`) — жорстко прошитих Linux-шляхів типу `/opt/...` у runtime-коді немає.
