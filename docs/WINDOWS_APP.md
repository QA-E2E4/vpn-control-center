# OpenFlux Desktop для Windows

`windows-client/` содержит приложение OpenFluxDesktop: Compose Multiplatform
UI, профили, импорт ссылок, подключение/отключение, статистику, настройки,
журналы и локальное ядро. Это готовый проект upstream, используемый здесь с
сохранёнными авторами и лицензиями. Панель управления сервером в него не
встроена.

## Требования

- Windows 10/11 x64.
- JDK17+, Go и Git для сборки ядра из исходников.
- Windows package требует WinDivert/Wintun assets, которые upstream workflow
  подготавливает из официальных источников.

## Запуск и тесты

Из `windows-client/`:

```powershell
.\gradlew.bat :shared:jvmTest
.\gradlew.bat -PskipCore=true :desktopApp:run
```

Режим `-PskipCore=true` не устанавливает туннель, пока пользователь не
выберет доступное ядро в настройках. Без этого параметра Gradle пытается
собрать подходящее ядро из исходников.

## MSI/EXE

```powershell
.\gradlew.bat -PwindowsPackage=true :desktopApp:packageMsi
.\gradlew.bat -PwindowsPackage=true :desktopApp:packageExe
```

Установочные артефакты содержат клиент, не серверную панель. Не распространяйте
свой VPN-профиль или ключ вместе с установщиком.

## Источник и лицензия

Исходный desktop UI и logic созданы на базе OpenFluxClient авторства
[@meepo161](https://github.com/meepo161/OpenFluxClient) и использованы в
OpenFluxDesktop с разрешения автора. Благодарности и лицензии см. в
`windows-client/README.md`, `windows-client/LICENSE` и `windows-client/NOTICE`.
