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
- `ONE_C_TIMEOUT`
- `ADMIN_IDS`

## 2) Ручний запуск (перевірка)

```powershell
.\venv\Scripts\Activate.ps1
python main.py
```

## 3) Запуск як Windows Service через NSSM

Перед установкою сервісу переконайтеся, що:
- файл `.env` уже створений і заповнений;
- ручний запуск `python main.py` проходить без помилок;
- `nssm.exe` доступний або в `PATH`, або за відомим повним шляхом.

Відкрийте `PowerShell` **від імені Адміністратора** і виконайте:

```powershell
cd C:\path\to\BOT_Miasokombinat
Set-ExecutionPolicy -Scope Process Bypass -Force
.\deploy\windows\install_meatbot_service.ps1 -ServiceName meatbot -NssmExe "C:\tools\nssm\nssm.exe"
```

Якщо `nssm.exe` вже є в `PATH`, параметр `-NssmExe` можна не передавати:

```powershell
.\deploy\windows\install_meatbot_service.ps1
```

Що робить скрипт:
- знаходить `ProjectRoot`, `python.exe` і `nssm.exe`;
- перевіряє наявність `main.py`;
- створює папку `logs`;
- встановлює або перевстановлює сервіс `meatbot`;
- запускає сервіс одразу після оновлення конфігурації.

Корисні команди перевірки:

```powershell
Get-Service meatbot
sc.exe qc meatbot
Get-Content .\logs\service_stderr.log -Tail 100
Get-Content .\logs\service_stdout.log -Tail 100
```

Оновлення після змін у коді:

```powershell
git pull
.\deploy\windows\install_meatbot_service.ps1 -ServiceName meatbot -NssmExe "C:\tools\nssm\nssm.exe"
```

Керування сервісом:

```powershell
Restart-Service meatbot
Stop-Service meatbot
Start-Service meatbot
```

Видалення сервісу:

```powershell
nssm remove meatbot confirm
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
