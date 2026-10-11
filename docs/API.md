# API панели

API — локальный административный интерфейс, не публичный сервис для
пользовательских приложений. Сначала откройте главную HTML-страницу панели:
она выдаёт короткоживущий token. Для запросов используйте его в заголовке
`X-Panel-Token`; POST также требует разрешённый `Host`/`Origin` и JSON.
Не помещайте token в URL, логи или клиентское desktop-приложение.

Основные маршруты из `panel/server.py`:

| Метод | Путь | Назначение |
|---|---|---|
| GET | `/api/state` | Состояния и профили доступных разделов |
| GET | `/api/profiles/{id}/logs` | Журнал OpenFlux-профиля |
| GET | `/api/happ/{id}/logs` | Журнал Happ-профиля |
| GET | `/api/amnezia/{id}/logs` | Журнал Amnezia-профиля |
| POST | `/api/profiles` | Создать OpenFlux-профиль |
| POST | `/api/happ` | Создать Happ-профиль |
| POST | `/api/amnezia` | Создать Amnezia-профиль |
| POST | `/api/settings` | Изменить адрес Direct-настройки |
| POST | `/api/refresh` | Обновить снимок состояния |
| POST | `/api/profiles/{id}/{start\|stop\|restart\|share\|delete\|edit\|labels}` | Действие над OpenFlux-профилем |
| POST | `/api/happ/{id}/{start\|stop\|share\|delete\|edit\|labels}` | Действие над Happ-профилем |
| POST | `/api/amnezia/{id}/{start\|stop\|share\|delete\|edit\|labels}` | Действие над Amnezia-профилем |
| POST | `/api/users/{id}/delete` | Удалить пользователя и назначенные устройства |

Точный JSON для создания/изменения определяется формой текущего UI; смотрите
`panel/static/app.js` и backend validation. Не используйте production API для
теста на рабочих пользователей. При успешной операции backend возвращает JSON
объект результата; ошибка валидации/операции возвращает HTTP400/500 с
обезличенным сообщением. API не гарантирует внешнюю доступность VPN.

Пример локального чтения состояния после открытия панели и безопасного
получения token внутри доверенного приложения не приводится намеренно:
token не должен попасть в shell history или документацию. Для обычного
администрирования используйте веб-интерфейс.
