# VPN Control Center

Локально запускаемая веб-панель для OpenFlux, Happ/Xray Reality и AmneziaWG,
а также исходники OpenFlux Desktop для Windows, macOS и Linux.

> Панель предназначена для собственного сервера и доступна только через
> localhost/SSH-туннель. Не публикуйте её в интернет без отдельной проверки
> аутентификации, TLS и модели доступа.

## Возможности

- Разделы OpenFlux, Happ и AmneziaWG с управлением доступом и профилями.
- QR/ссылки и конфигурация профиля, когда это поддерживает протокол.
- Индивидуальная статистика по credential/peer, если сервер предоставляет
  соответствующие счётчики; неподтверждённая статистика не выдаётся за ноль.
- Удаление с серверным отзывом доступа, подтверждение и обработка частичных
  ошибок. Проверяйте backend и ограничения выбранного сервиса.
- Интерфейс на русском, адаптивная вёрстка, локальные статические ресурсы.
- Экспериментальный транспорт Telemost в OpenFlux 0.5.3 панели; одна
  отдельная конференция на клиент и сервер. Совместимость iOS не проверена.
- OpenFlux Desktop — отдельное GPL-приложение, подключённое в `windows-client/`
  из официального репозитория; исходники и авторство сохранены.

Ограничения: привязку ключа Happ к физическому устройству надёжно реализовать
без идентификатора/аттестации клиента нельзя. Панель не блокирует пользователей
по IP или числу TCP-соединений. Работа транспорта при ограничениях оператора
зависит от конкретной сети; гарантий обхода нет. Демо не управляет реальным
VPN. Тестовая эксплуатация на одном сервере не заменяет аудит безопасности.

## Состав

```text
panel/                 Python API, статический UI и unit-тесты
windows-client/        OpenFlux Desktop (Compose Multiplatform, GPL-3.0-or-later)
docs/                  архитектура, настройка, API, безопасность и разработка
.github/workflows/     проверки backend/frontend и Windows Desktop
```

Панель и desktop-клиент — самостоятельные приложения. Desktop-клиент не
администрирует backend панели и не получает её секреты. Он запускает ядро
OpenFlux локально и хранит пользовательские профили на устройстве.

## Требования

- Linux VPS для production панели: systemd, Python 3.10+, OpenFlux core.
- Для соответствующих интеграций: Docker с уже настроенными Xray Reality
  и/или AmneziaWG. Панель не устанавливает и не перенастраивает их автоматически.
- Для демо и backend-тестов: Python 3.10+, `qrcode`, Node.js 20+.
- Для Windows Desktop: Windows 10/11, JDK 17+, Go, Git; Android Studio не нужен.

## Быстрый запуск демонстрации

В PowerShell или терминале проекта:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r panel\requirements.txt
python panel\server.py --demo --data .\demo-data --port 8787
```

Откройте <http://127.0.0.1:8787>. Демо создаёт локальную SQLite и синтетические
профили. Оно не запускает `systemctl`, Docker, OpenFlux, Xray или AmneziaWG.
Остановить можно Ctrl+C.

## Production: безопасный доступ

Сначала изучите [SETUP.md](docs/SETUP.md) и [DEPLOYMENT.md](docs/DEPLOYMENT.md).
Production-панель должна слушать только `127.0.0.1`. Открывайте её локально
через SSH-туннель, например:

```powershell
ssh -N -L 8787:127.0.0.1:8787 admin@your-vps.example.net
```

Не запускайте пример production unit, пока не проверены пути, отдельные
системные пользователи, права на socket/данные и интеграции конкретного VPS.
Панель имеет административные права на выбранные серверные действия.

## Windows Desktop

```powershell
cd windows-client
.\gradlew.bat :shared:jvmTest
.\gradlew.bat -PwindowsPackage=true :desktopApp:packageMsi
```

Для запуска без сборки ядра: `.\gradlew.bat -PskipCore=true :desktopApp:run`.
Полные инструкции — [WINDOWS_APP.md](docs/WINDOWS_APP.md).

## Проверки

```powershell
python -m pip install -r panel\requirements.txt
python -m unittest discover -s panel\tests -p "test_*.py" -v
node panel\tests\frontend.test.js
node --check panel\static\app.js
```

GitHub Actions повторяет эти проверки и запускает JVM-тесты/сборку Windows MSI.
Результаты каждого запуска смотрите во вкладке Actions.

## Документация

- [Архитектура](docs/ARCHITECTURE.md)
- [Установка и настройка](docs/SETUP.md)
- [Production-развёртывание](docs/DEPLOYMENT.md)
- [Windows Desktop](docs/WINDOWS_APP.md)
- [API панели](docs/API.md)
- [Безопасность](docs/SECURITY.md)
- [Участие в разработке](docs/CONTRIBUTING.md)
- [История изменений](docs/CHANGELOG.md)
- [План работ](docs/ROADMAP.md)

## Лицензия и происхождение

Проект распространяется по GNU GPL версии 3 или новее в соответствии с
включёнными компонентами. Сохраняйте `LICENSE`, `NOTICE` и лицензии внутри
`windows-client/`. Интерфейс Desktop основан на OpenFluxClient автора
[@meepo161](https://github.com/meepo161/OpenFluxClient) и используется
OpenFluxDesktop с согласия автора; см. credits в
[`windows-client/README.md`](windows-client/README.md). Ядро OpenFlux —
отдельный компонент проекта [p1neappleXpress/OpenFlux](https://github.com/p1neappleXpress/OpenFlux).
