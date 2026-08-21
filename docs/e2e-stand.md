# GBA E2E стенд (gba-e2e)

Ізольований зріз для браузерних E2E-сценаріїв консолі: окремі контейнери + клони БД на dev-MSSQL-інстансі. Живі dev-бази й parity-аудит не зачіпаються; 1С-конекшнів у стенда немає.

## Топологія

| Сервіс | Порт | Призначення |
|---|---|---|
| `gba-console-e2e` | 127.0.0.1:8084 | консоль (`gba-console:e2e-stand` із exact git-SHA), проксі на e2e-бекенди |
| `data-concord-e2e` | 127.0.0.1:35991 | API (`gba-data-concord:**e2e**` — патч env-конфіг control-БД), шедулери OFF, background writers ON |

> **Образ `gba-data-concord:e2e-stand`**: бек хардкодив `IF DB_NAME() <> N'ConcordDb_V5' THROW` у лізі реконсиляції консигнацій (`ProductIncomeRepository`, 54603), що валило оприходування/продаж/повернення на `_E2E`-базі. Патч робить імʼя контрольної БД env-конфігурованим (`GBA_CONTROL_DATABASE_NAME`, дефолт `ConcordDb_V5` — dev не змінюється). Стенд ставить `GBA_CONTROL_DATABASE_NAME=ConcordDb_V5_E2E`. Перезбирати образ після змін бекенду з exact label: `cd /root/projects/gba-server && docker build --label gba.git.sha="$(git rev-parse HEAD)" --build-arg PROJECT=Global.Business.Assistant.Api -t gba-data-concord:e2e-stand .`. **Застереження**: `CaptureStockSnapshotActor` (54602) досі хардкодить `ConcordDb_V5`, але він scheduler-gated — НЕ вмикати шедулери на e2e-стенді.
| `data-analytics-e2e` | 127.0.0.1:35994 | history/report (35992 зайнятий `reports-v9-analytics`) |

БД на `gba-dev-gba-mssql-1`: `ConcordDb_V5_E2E`, `ConcordIdentityDb_E2E`, `ConcordDb_Data_E2E`, `GbaVehicleRegistry_E2E`. Кожна позначена extended property `GbaE2EStandDb`; усі скрипти стенда відмовляються працювати з БД без суфікса `_E2E` і маркера, і поза інстансом `@@SERVERNAME = 01934d77f334`. Один lifecycle run/reset/golden-refresh додатково тримає міжпроцесний lock `/var/lock/gba-e2e-stand.lock`, тому паралельний reset не може обнулити бази посеред Playwright-прогону.

Запуск стека:

```bash
cd /root/projects/gba-infra
docker compose -p gba-e2e -f docker-compose.e2e.yml --env-file .env.e2e up -d --wait
```

## Скрипти (`scripts/e2e/`)

- `create-golden.sh [--skip-migrator]` — переливає golden-набір БД: V5+Identity з експорту `/var/opt/mssql/backup/gba-dev-export-<stamp>/`, Data+VehicleRegistry свіжим COPY_ONLY-беком з dev; `RECOVERY SIMPLE` + shrink лога; ставить маркер із git-sha образів; ганяє EF-мігратор (очікувано no-op — не no-op означає дрифт схеми). Не редагувати скрипт під час його виконання.
- `gen-e2e-secrets.sh` — перегенеровує `secrets/e2e/` з `secrets/dev/` (замінюється тільки `Database=`); ганяти після ротації dev-секретів.
- `e2e-reset.sh snapshot|revert|status|drop-snapshots` — reset-механіка: снапшоти всіх 4 БД і відкат (~30 с: стоп e2e-бекендів → SINGLE_USER → RESTORE FROM SNAPSHOT → старт → health). Revert вимагає рівно один снапшот на БД. `revert --prune-uploads` додатково чистить файловий volume `/app/Data` (БД-відкат файли не чіпає). Якщо revert упав між стопом і стартом — бекенди лишаються зупинені; наступний успішний revert або `up -d` їх підніме.
- `seed-e2e-user.sh` — скидає пароль `admin.local@gba.test` ТІЛЬКИ в `ConcordIdentityDb_E2E` і пише креди в `/etc/gba-e2e.env` (0600). Після нього перезняти снапшоти (`drop-snapshots` + `snapshot`), щоб пароль пережив reverts.
- `run-e2e.sh smoke|full|--spec <path>` — оркестратор: preflight (порти, БД+маркери, один снапшот, exact SHA й чистота канонічного console checkout, warn при дрифті образів проти golden-маркера) → `up --wait` → revert → Playwright → архів звіту в `gba_console/output/e2e-reports/<ts>-<mode>/`.

## Playwright (у репо gba_console)

- `npm run e2e:smoke` / `e2e:full` / `e2e:report`; конфіг `playwright.config.ts` (проекти setup → smoke/full, storageState).
- `E2E_BASE_URL` ТІЛЬКИ `http://localhost:<port>` — кукі `__Host-*` Secure і не приймаються з інших origin.
- Env: `E2E_USERNAME`/`E2E_PASSWORD` (з `/etc/gba-e2e.env`), `E2E_SQL_PASSWORD` (= `SQL_SA_PASSWORD` з `.env.dev`; runner підхоплює сам).
- Фікстура `db` має власний fence: суїта падає одразу, якщо підключена не до `_E2E`-бази.
- Фікстури приходу: `gba_console/SQL/TestIncome/` (вантажити можна лише `CCD_* — копия.xlsx`; контроль — README.md + AUDIT-2026-07-22.md).

## Nightly

`gba-e2e-nightly.service` + `.timer` (02:30, до parity-cron о 05:30). Увімкнути після стабілізації сюїти:

```bash
systemctl daemon-reload && systemctl enable --now gba-e2e-nightly.timer
```

Service має запускати Playwright із канонічного чистого checkout, зараз це
`CONSOLE_ROOT=/root/deploy/gba-console-aug21`. Не спрямовувати nightly на тимчасовий
або dirty worktree: застосунок у контейнері та тести на host повинні мати один git SHA.

## Освіження golden

Робити щомісяця або після великих міграцій/змін довідників: свіжий експорт БД → `create-golden.sh` → `seed-e2e-user.sh` → `e2e-reset.sh snapshot`. Preflight у `run-e2e.sh` нагадає варнінгом, коли образи втечуть від golden-маркера.

## Відомі деградації (прийнято свідомо)

- AI-панелі консолі ходять у живий фліт (:8000–8006), який читає dev-БД — дані в цих панелях не збігаються зі стендом; сторінки мають деградувати граційно.
- Бейдж QA Desk (`/qa-desk/api/builds/current`) доступний через спільну dev-мережу; console лишається одночасно підключеною до ізольованої мережі стенда для backend API.
- Elasticsearch спільний із dev (шедулери вимкнені, ядро консольних флоу в ES не пише).
- Файли, створені тестами в `/app/Data`, не відкочуються снапшотом БД (`--prune-uploads` за потреби).

## Покриття сюїти (усі кроки з DB-інваріантами)

| Спека | Домен | Перевіряє |
|---|---|---|
| `00-shell` | оболонка | логін/навігація, нуль pageerror |
| `f1-income/10-14` | прихід + оприходування | 7 реальних форматів постачальників: замовлення→проформа→інвойс→пакліст→митна специфікація→прихід + консигнації (партії==позиції, twin-guard) |
| `f1-income/16` | повторний імпорт | повторна специфікація не змінює кількість/суми специфікацій, приходів або консигнацій |
| `f2-sales/20` | продаж | візард «Нова продажа» → рядок замовлення з нашим товаром |
| `f3-returns/30` | повернення | «Оплата покупця»-return: рознесення на продаж, +1 SaleReturnItem |
| `f4-warehouse/40` | склад | переміщення: source `ProductPlacement`−qty, dest `ProductAvailability`+qty, availability збережено |
| `f5-cash/50` | каса/FX | оплата покупця → IncomePaymentOrder + рознесення на борг + PaymentOrderFxSnapshot |
| `cross/60` | наскрізний | канарейка продаж→повернення того самого товару |

## Закриті регресії та важливі інваріанти

- **Плейсхолдери специфікації прибираються.** Після завантаження митних кодів є рівно одна активна і одна реальна специфікація на рядок інвойса, з exact FK на `SupplyInvoiceOrderItem`. Спека 13 перевіряє `activeSpecs == realSpecs == invoice rows`; старе спостереження про дві специфікації було зі старого/брудного образу.
- **Реаплоуд митної специфікації ідемпотентний.** Спека 16 доводить `after == before` по специфікаціях, приходах і консигнаціях. Це звичайний зелений тест, не `expected-fail`.
- **Курс перевіряється за валютою інвойса й exact датою МД.** Для EUR на 2026-07-20 очікується `51.0595` (а не курс наступного дня `51.0955`), для USD — `44.6676`. Усі рядки одного паклиста повинні мати один курс.
- **Формати CCD різні.** AYMEKS має брутто/нетто у колонках 4/5, інші шість fixture — 5/6. Колонка 4 в них є назвою товару й не може передаватися як числова вага.
- **Великі інвойси потребують масштабованого test timeout.** Розміщення навмисно проходить UI-дровер для кожної позиції; FSS має 106, REMI MAY 122 рядки. Двохвилинний глобальний timeout не є доменним fail.
- **nginx без `client_max_body_size`** (1 МБ дефолт) → HTTP 413 на великих JSON розміщення/пакліста. Пофікшено в `gba_console/nginx.conf` (64m) — прод-релевантно.
- **Хардкод `ConcordDb_V5`** у бек-гарді (legacy consignment reconciliation) → env `GBA_CONTROL_DATABASE_NAME` (образ `gba-data-concord:e2e-stand`).
- **Пошук контрагента (`/clients/all/filtered`)** — це префікс по FullName, не Name/ЄДРПОУ; деякі прізвища колізять (МАМИЧ→МАГРОМ ТОВ). Для юзерів варто перевірити зручність пошуку ФОП.

Останній чистий proof 2026-08-21: smoke `13/13`; повна матриця приходу
`30/30` за 27.6 хв (7 постачальників, 507 UI-розміщень, EUR/USD, outbox,
консигнації та replay). Звіти архівуються runner-ом у `output/e2e-reports/`.

## Нотатки автоматизації (крихкі місця UI)

- Друм-каруселі (клієнт у візарді) рендерять рядок поза вьюпортом → `locator.dispatchEvent('click')`, не `.click({force})`.
- Mantine searchable Select = `role=combobox` з чистим ім'ям (без «*»); у формі оплати клієнта друкувати `pressSequentially` (не `fill`) — інакше option-submit guard скидає вибір.
- Візардний «Доступна К-сть» (ATP) набагато менший за суму консигнацій — флоу читає її з модалки й продає min(запит, ATP); асерт кількості — «додатна», не точна.
- **Повернення:** дровер вимагає вибрати Клієнта (пошук по слову з FullName, не USREOU — Mantine ще й клієнт-сайд фільтрує опції по лейблу) + «Артикул» (пошук стрипає дефіси зі SearchVendorCode → шукати код без дефісів). Правило повертабельності бекенду: `Sale.ChangedToInvoice IS NOT NULL` + не-1С-бухгалтерський. Редактор позиції: вибір «Причини» перевантажує склади й скидає «Кількість» → ставити кількість ОСТАННЬОЮ.
- **Ключове про повернення — предрезолв складу:** НЕ кожна повертабельна позиція має склад повернення для довільної причини (статус 6 «Брак» іде тільки в `ForDefective`-склад; регулярні причини — тільки в звичайний). Сліпий перебір причин/рядків у UI вибивав 120-с таймаут. Тому `flows/sales.ts:resolveCompletableReturn` спершу через `page.request` пробиває ті самі два ендпоінти, що й сторінка (`/sales/all/returns/search` → `/storages/all/returns/filtered`) і повертає позицію, для якої бек ПІДТВЕРДИВ непорожній список складів під конкретний статус; далі UI робить ОДНУ спробу з відомо-доброю причиною. Наскрізний (`cross/60`) додатково фільтрує резолвер по `vendorCode`, щоб продати й повернути ТОЙ САМИЙ товар; його per-test timeout піднято до 300 с (дві UI-подорожі).

## ⚠️ ГОЛОВНИЙ ОПЕРАЦІЙНИЙ УРОК: перебілдовуй e2e-образи після будь-яких змін бекенду

Стара збірка backend мовчки ламала пошук повернень (повертала 0 на валідних даних), а спільний тег дозволяв паралельному білду непомітно підмінити образ стенда. Стенд тому використовує окремі теги `gba-data-concord:e2e-stand` і `gba-console:e2e-stand`; звичайні `:e2e` білди їх не перетирають. **Після зміни backend або console перебудуй відповідний stand-образ із `--label gba.git.sha="$(git rev-parse HEAD)"`** і зроби `docker compose ... up -d --force-recreate`. `run-e2e.sh` відмовляється стартувати без exact label і попереджає про розбіжність із golden-маркером.
