# GBA E2E стенд (gba-e2e)

Ізольований зріз для браузерних E2E-сценаріїв консолі: окремі контейнери + клони БД на dev-MSSQL-інстансі. Живі dev-бази й parity-аудит не зачіпаються; 1С-конекшнів у стенда немає.

## Топологія

| Сервіс | Порт | Призначення |
|---|---|---|
| `gba-console-e2e` | 127.0.0.1:8084 | консоль (той самий образ `gba-console:latest`), проксі на e2e-бекенди |
| `data-concord-e2e` | 127.0.0.1:35991 | API (`gba-data-concord:**e2e**` — патч env-конфіг control-БД), шедулери OFF, background writers ON |

> **Образ `gba-data-concord:e2e`**: бек хардкодив `IF DB_NAME() <> N'ConcordDb_V5' THROW` у лізі реконсиляції консигнацій (`ProductIncomeRepository`, 54603), що валило оприходування/продаж/повернення на `_E2E`-базі. Патч робить імʼя контрольної БД env-конфігурованим (`GBA_CONTROL_DATABASE_NAME`, дефолт `ConcordDb_V5` — dev не змінюється). Стенд ставить `GBA_CONTROL_DATABASE_NAME=ConcordDb_V5_E2E`. Перезбирати образ після змін бекенду: `cd /root/projects/gba-server && docker build --build-arg PROJECT=Global.Business.Assistant.Api -t gba-data-concord:e2e .`. **Застереження**: `CaptureStockSnapshotActor` (54602) досі хардкодить `ConcordDb_V5`, але він scheduler-gated — НЕ вмикати шедулери на e2e-стенді.
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
- `run-e2e.sh smoke|full|--spec <path>` — оркестратор: preflight (порти, БД+маркери, один снапшот, warn при дрифті git-sha образів проти golden-маркера) → `up --wait` → revert → Playwright → архів звіту в `gba_console/output/e2e-reports/<ts>-<mode>/`.

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

## Освіження golden

Робити щомісяця або після великих міграцій/змін довідників: свіжий експорт БД → `create-golden.sh` → `seed-e2e-user.sh` → `e2e-reset.sh snapshot`. Preflight у `run-e2e.sh` нагадає варнінгом, коли образи втечуть від golden-маркера.

## Відомі деградації (прийнято свідомо)

- AI-панелі консолі ходять у живий фліт (:8000–8006), який читає dev-БД — дані в цих панелях не збігаються зі стендом; сторінки мають деградувати граційно.
- Бейдж QA Desk (`/qa-desk/api/builds/current`) на стенді віддає 502 — desk недоступний з мережі gba-e2e.
- Elasticsearch спільний із dev (шедулери вимкнені, ядро консольних флоу в ES не пише).
- Файли, створені тестами в `/app/Data`, не відкочуються снапшотом БД (`--prune-uploads` за потреби).
