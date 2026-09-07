# DEV: оцінка фізичних залишків за договором

Набір8 додає поточну EUR-оцінку кожного фізичного розміщення за одним точним ClientAgreement.Id. Договір — параметр ціни; власник залишку ним не підміняється. Невизначені ціни зберігають кількість і залишають неповні грошові підсумки порожніми.

Images:
- Analytics: `gba-data-analytics:agreement-valuation-4cf11008`, revision `4cf1100828523ea2fa6445151684795388b391f5`.
- Console: `gba-console:agreement-valuation-024b58a5`, revision `024b58a5e00e91481f220cfaaef7fa6919f91b31`.

Перевірки до розгортання: server386/386, console272/272, build/lintgreen. Doctor90→90 на однакових18 існуючих файлах;8нових100/0issues. Native reader12133товари,11847цін підтверджено,286unknown; індивідуальні вкладення відповідають чинному engine. Структура1С377об'єктів і повна семантична відповідність не вважаються завершеними.

Застосування: додати compose-overlay наприкінці фактичного списку overlays кожного сервісу; окремо `up -d --no-deps --no-build data-analytics` та `gba-console`. DataConcord не перезапускати. Міграції, бізнес-дані,1С та налаштування існуючих БД цей release не змінює.

Повернення: попередні images `gba-data-analytics:stock-lots-3195c00b` і `gba-console:stock-lots-094cd30e`; попередні фактичні config chains/container IDs збережено у private `deployment-before.json`. Новий шаблон набору8 потребує версії, що підтримує цей набір.

Live browser/XLSX/PDF acceptance: pending після healthy rollout; evidence `/root/evidence/report-port-wave7-2026-09-07/`.
