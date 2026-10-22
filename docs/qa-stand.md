# GBA QA стенд (gba-qa)

Пісочниця для ручного тестування: повна копія DEV-баз, у якій можна вільно створювати й змінювати документи. DEV лишається основною базою з даними 1С; зміни в QA туди не потрапляють.

## Як користуватись

У шапці консолі, біля імені користувача, є перемикач **DEV | QA**. Вибір ставить cookie `gba_env` і перезавантажує сторінку. У режимі QA шапка має фіолетову смугу зверху.

Логін один на обидва режими: вхід, оновлення токена й вихід завжди йдуть на DEV-бекенд, а QA-бекенд приймає той самий підписаний токен. Тому користувачі, створені в DEV після клону, у QA не існують, поки QA не оновлено.

## Топологія

| Що | Де |
|---|---|
| `data-concord-qa` | 127.0.0.1:35996, мережа `gba-dev_default`, той самий образ, що й DEV `data-concord` |
| `data-analytics-qa` | 127.0.0.1:35997, той самий образ, що й DEV `data-analytics` |
| Бази на `gba-dev-gba-mssql-1` | `ConcordDb_V5_QA`, `ConcordIdentityDb_QA`, `ConcordDb_Data_QA`, `GbaVehicleRegistry_QA`, файли в `/var/opt/mssql/data/qa` |
| Стан і секрети | `/private/gba-qa` (згенерований compose, рядки підключення з `_QA`, файли `/app/Data`, `/app/Images`, `/app/Logs`) |
| Роутинг | nginx консолі: `map $cookie_gba_env` → `API_QA_PROXY_TARGET` / `API_QA_HISTORY_PROXY_TARGET`; `GBA_QA_ENABLED=true` вмикає перемикач (`/gba-env.json`) |

Кожна QA-база має extended property `GbaQaStandDb`. Скрипти відмовляються працювати поза інстансом `01934d77f334`, з базою без суфікса `_QA` або без маркера.

## Що вимкнено в QA

- Жодних підключень до 1С: секрети `*OneC*` не передаються, усі прапорці синку, захоплення й імпорту примусово `false`. Застосунок читає лише базу GBA; з 1С працює тільки синк DEV.
- Шедулери вимкнені, maintenance-режим вимкнений, `GBA_CONTROL_DATABASE_NAME=ConcordDb_V5_QA`.
- Background writers увімкнені: списання, outbox і решта ефектів продажів працюють як на DEV.
- 1С забирає експорт тільки з DEV, бо QA не має публічної адреси.

NBA ізольовано: DEV `gba-nba.service:8002` читає `ConcordDb_V5` і Mongo `gba_nba`; QA `gba-nba-qa.service:8012` читає `ConcordDb_V5_QA` і Mongo `gba_nba_qa`. Задачі, контакти та зв'язки продажів у QA не змінюють DEV. QA не запускає генерацію, workers або timers автоматично. Перевірка маршрутизації, SQL-доступу та незалежності Mongo: `scripts/qa/verify-nba-isolation.py`.

Решта AI-сервісів (рекомендації, solvency, pricing, products, forecast, procure) спільні й читають DEV. Їхні панелі не є доказом ізоляції QA-даних. Для оновлення лише web-процесу закупівель встановлюється drop-in `systemd/gba-procure-web-only.conf` у `/etc/systemd/system/gba-procure.service.d/web-only.conf`: `WARM_ON_STARTUP=false` не запускає новий розрахунок під час перезапуску. Налаштування окремих workers і scheduler при цьому не змінюються.

## Скрипти (`scripts/qa/`)

- `qa-refresh.sh`: повне оновлення QA з поточного DEV. Зупиняє QA-бекенди, клонує 4 бази, копіює `Data`/`Images` з DEV і запускає бекенди. Усе, що було зроблено в QA, зникає.
- `qa-clone.sh`: тільки бази. На кожну базу: COPY_ONLY-бекап з DEV, видалення старої `_QA` (за маркером), відновлення, `RECOVERY SIMPLE`, лог до 1 ГБ, маркер, видалення `.bak`. Відмовляється працювати, якщо лог `ConcordDb_V5` більший за `QA_MAX_SOURCE_LOG_GB` (40): відновлення мусило б занулити такий самий лог. Спочатку стисни лог: `gba-docs/scripts/shrink-dev-v5-log.sh`.
- `qa-up.sh`: генерує compose з живого DEV compose (`QA_DEV_COMPOSE`) і перестворює QA-бекенди на тих самих образах і конфігурації, з перевизначеннями для QA. **Запускати після кожного деплою DEV-бекенду**, щоб QA йшов на тому ж коді.
- `gen-qa-compose.py`: генератор для `qa-up.sh`.

Усі скрипти тримають lock `/var/lock/gba-qa-stand.lock`.

## Схема бази

Клон отримує ту саму схему й `__EFMigrationsHistory`, що й DEV на момент клону. Якщо міграцію накочено на DEV після клону, накоти її й на `ConcordDb_V5_QA` або зроби `qa-refresh.sh`.
