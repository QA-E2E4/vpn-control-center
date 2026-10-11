# Установка и первоначальная настройка

## Локальная панель

Нужен Python 3.10+.

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r panel\requirements.txt
python panel\server.py --demo --data .\demo-data --port 8787
```

Откройте `http://127.0.0.1:8787`. Демо создаёт синтетические записи и
не исполняет команды управления ОС/VPN. Для остановки нажмите Ctrl+C.

## Linux production

Production требует Linux с systemd и уже установленными/настроенными VPN
сервисами. Установите Python 3.10+, зависимости из `panel/requirements.txt`,
ядро OpenFlux и поместите код в каталог, доступный выделенной учётной записи.
Не используйте демо-режим.

Скопируйте `.env.example` в закрытый EnvironmentFile, замените значения на
фактические пути этого хоста и установите права чтения только для службы.
Обязательные каталоги данных должны принадлежать выделенному пользователю;
private key/cookie файлы должны быть доступны только служебным аккаунтам.

Минимальная команда для первого запуска API:

```sh
python3 /opt/vpn-control-center/panel/server.py --port 8787
```

Она слушает loopback. Для постоянной эксплуатации настройте systemd, права,
`ReadWritePaths`, каталог runtime IPC, лимиты и резервное копирование по
`DEPLOYMENT.md`. Не запускайте от root до отдельной оценки команд, которые
панель выполняет в вашем окружении.

## Тестовые переменные

`OPENFLUX_CORE_BINARY`, `OPENFLUX_PANEL_DATA_DIR`,
`OPENFLUX_PANEL_CHANNELS_DIR`, `OPENFLUX_IPC_DIR` и
`OPENFLUX_SYSTEMD_UNIT_DIR` позволяют задать пути хоста. Переменные Xray и
Amnezia включайте только для существующих сервисов и после проверки их API,
контейнеров, mounts и конфигурации. `.env.example` не содержит действующих
серверных данных.

## Windows Desktop

Установите JDK17+, Go и Git. Из `windows-client/` выполните:

```powershell
.\gradlew.bat :shared:jvmTest
.\gradlew.bat -PwindowsPackage=true :desktopApp:packageMsi
```

Инструкции и варианты запуска без автоматически собранного ядра находятся в
`WINDOWS_APP.md` и официальном `windows-client/README.md`.
