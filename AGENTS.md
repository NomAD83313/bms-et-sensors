# AGENTS.md — BMS ET Sensors Stack
EN: User wants answers in Russian. All code and comments must be in English.

## Purpose / Назначение

EN: This file defines a shared operating guide for coding agents working in this repository. It complements `README.md` (product/runtime docs) and `CLAUDE.md` (assistant-specific context) with practical implementation rules that should stay model-agnostic.

RU: Этот файл задает общий рабочий регламент для кодовых агентов в данном репозитории. Он дополняет `README.md` (документация по продукту и рантайму) и `CLAUDE.md` (контекст для ассистента) практическими правилами, не привязанными к конкретной модели.

## Project summary / Кратко о проекте

EN: Docker-based sensor acquisition and visualization stack for Linux hosts (Raspberry Pi is a primary target).

RU: Docker-стек для сбора и визуализации данных датчиков на Linux-хостах (основная целевая платформа — Raspberry Pi).

EN: Core custom services:
- `mscl-collector`: MicroStrain MSCL stream/config API.
- `redlab-collector`: MCC RedLab thermocouple collector.
- `almemo-collector`: ALMEMO serial integration.
- `pyrometer-collector`: thermoMETER CT / Optris serial integration.
- `graf-lite`: lightweight dashboard for low-resource hosts.
- `service-controller`: service control and recovery logic (RedLab, ALMEMO, and MSCL safeguards).

RU: Основные кастомные сервисы:
- `mscl-collector`: API конфигурации и стриминга MicroStrain MSCL.
- `redlab-collector`: сборщик термопар MCC RedLab.
- `almemo-collector`: интеграция последовательных устройств ALMEMO.
- `pyrometer-collector`: интеграция thermoMETER CT / Optris.
- `graf-lite`: облегченный дашборд для малоресурсных хостов.
- `service-controller`: контроль сервисов и логика восстановления (защита RedLab, ALMEMO и MSCL).

EN: Third-party services: `influxdb`, `grafana`, `dashboard` (`nginx` + simple-dash).

RU: Сторонние сервисы: `influxdb`, `grafana`, `dashboard` (`nginx` + simple-dash).

EN: Optional profile-gated services (Thread / Matter):
- `openthread-border-router`: OpenThread Border Router; Compose profile `thread`; SonoffE RCP dongle; host networking.
- `matter-server`: Home Assistant python-matter-server; Compose profile `matter`; host networking.
- `matter-collector`: Matter collector app; Compose profile `matter`; writes normalized sensor values to InfluxDB from Matter Server websocket events.

RU: Опциональные сервисы (профили Thread / Matter):
- `openthread-border-router`: OpenThread Border Router; профиль `thread`; донгл SonoffE RCP; host networking.
- `matter-server`: Home Assistant python-matter-server; профиль `matter`; host networking.
- `matter-collector`: коллектор Matter; профиль `matter`; пишет нормализованные значения датчиков в InfluxDB из websocket-событий Matter Server.

## Repository map / Карта репозитория

- `app/mscl/`: EN MSCL Flask app and helpers. / RU MSCL Flask-приложение и хелперы.
- `app/redlab/`: EN RedLab collector. / RU сборщик RedLab.
- `app/almemo/`: EN ALMEMO app and minimal UI. / RU ALMEMO-приложение и минимальный UI.
- `app/pyrometers/`: EN pyrometer integration app. / RU приложение интеграции пирометров.
- `app/graf/`: EN Graf App Lite. / RU Graf App Lite.
- `app/matter/`: EN Matter collector app. / RU приложение-коллектор Matter.
- `app/svcctl/`: EN internal service-control API. / RU внутреннее API управления сервисами.
- `dashboard/`: EN nginx and simple-dash assets. / RU nginx и ресурсы simple-dash.
- `grafana/`: EN provisioning for datasources and dashboards. / RU provisioning для источников данных и дашбордов.
- `scripts/`: EN setup/build/restart/ops scripts. / RU скрипты настройки, сборки, перезапуска и эксплуатации.
- `scripts/lib/`: EN shared shell helpers. / RU общие shell-хелперы.
- `tests/`: EN unit tests for service/helper logic. / RU unit-тесты для сервисной и helper-логики.
- `docs/`: EN device manuals and notes. / RU мануалы и заметки по оборудованию.
- `docs/openthread.md`: EN OpenThread Border Router setup and Matter integration notes. / RU заметки по настройке OTBR и интеграции Matter.
- `scripts/openthread-host-setup.sh`: EN host prereq checker/applier for OTBR (IPv6, TUN, runtime dir). / RU проверка и настройка хоста для OTBR.
- `scripts/restart-openthread.sh`: EN start openthread-border-router (runs host check first). / RU запуск openthread-border-router.
- `scripts/restart-matter-server.sh`: EN start matter profile services (`matter-server` + `matter-collector`). / RU запуск сервисов профиля matter (`matter-server` + `matter-collector`).

## Non-negotiable rules / Обязательные правила

- EN Do not commit runtime state or local secrets.
  RU Не коммитить runtime-состояние и локальные секреты.
- EN Keep install-time templates tracked; keep runtime-generated files local.
  RU Шаблоны для установки хранить в Git, runtime-файлы держать только локально.
- EN Do not break Raspberry Pi workflows when changing compose/scripts.
  RU Не ломать workflow для Raspberry Pi при изменениях compose/скриптов.
- EN Prefer minimal, scoped changes over broad refactors.
  RU Предпочитать минимальные и локальные изменения вместо широких рефакторингов.
- EN Preserve existing service names and compose wiring unless migration is explicitly required.
  RU Сохранять текущие имена сервисов и связность в compose, если миграция не требуется явно.

## Install-time vs runtime policy / Политика install-time и runtime

EN: Tracked templates (install-time):
- `.env.example`

RU: Отслеживаемые шаблоны (install-time):
- `.env.example`

EN: Local runtime files (never commit):
- `.env`
- runtime artifacts under `runtime/`

RU: Локальные runtime-файлы (никогда не коммитить):
- `.env`
- runtime-артефакты в `runtime/`

EN: When adding a new mounted config/data path:
1. Classify it immediately as install-time or runtime.
2. Update `.gitignore` in the same change if runtime.
3. If install-time, provide a template and bootstrap path in scripts.

RU: При добавлении нового монтируемого пути config/data:
1. Сразу классифицировать путь как install-time или runtime.
2. В том же изменении обновить `.gitignore`, если это runtime.
3. Если это install-time, добавить шаблон и bootstrap-логику в скриптах.

## Standard commands / Стандартные команды

EN/RU Primary lifecycle / Основной цикл:
- `./scripts/restart-local.sh`
- `./scripts/build-local-all.sh`
- `./scripts/restart-mscl-dev.sh`

EN/RU Service-scoped builds / Сборка по сервисам:
- `./scripts/build-local-mscl.sh`
- `./scripts/build-local-redlab.sh`
- `./scripts/build-local-graf-app.sh`
- `./scripts/build-local-almemo.sh`
- `./scripts/build-local-pyrometers.sh`
- `./scripts/build-local-svcctl.sh`

EN/RU Diagnostics / Диагностика:
- `./scripts/logs.sh`
- `./scripts/logs.sh <service-name>`

EN/RU Tests / Тесты:
- `python -m unittest discover -s tests -q`

EN/RU Compose baseline / Базовый запуск compose:
- `docker compose -f docker-compose.yml -f docker-compose.override.yml up -d`

## Agent workflow / Процесс работы агента

EN:
1. Read the task and inspect only relevant files first.
2. Check for existing local changes with `git status --short`.
3. Implement the smallest coherent patch.
4. Run targeted checks first, then broader tests when needed.
5. Report what changed, why, and any residual risk.

RU:
1. Прочитать задачу и сначала изучить только релевантные файлы.
2. Проверить локальные изменения через `git status --short`.
3. Внести минимальный целостный патч.
4. Сначала запустить точечные проверки, затем более широкие тесты при необходимости.
5. Отчитаться, что изменено, зачем и какие остаточные риски остались.

## Change guidance by area / Рекомендации по зонам

EN: MSCL (`app/mscl`, `tests/`):
- Keep helper/service split consistent with current module style.
- Add or update unit tests for behavior changes.
- Prefer deterministic helpers and explicit fallback behavior.

RU: MSCL (`app/mscl`, `tests/`):
- Сохранять текущий стиль разделения helper/service модулей.
- Добавлять или обновлять unit-тесты при изменении поведения.
- Предпочитать детерминированные хелперы и явное fallback-поведение.

EN: ALMEMO (`app/almemo`, `docs/almemo`, `tests/`):
- Treat serial stability as the first priority; performance work must not reintroduce device freeze/dropout behavior.
- Prefer server-side batching for multi-step UI/device actions over many independent serial requests.
- Be explicit about serial flow-control assumptions (`XON/XOFF`) and test the smallest risky change first.

RU: ALMEMO (`app/almemo`, `docs/almemo`, `tests/`):
- Считать стабильность последовательного канала приоритетом номер один; оптимизация скорости не должна возвращать зависания/отвалы прибора.
- Для многошаговых UI/device-действий предпочитать серверный batching вместо множества независимых serial-запросов.
- Явно учитывать допущения по flow-control (`XON/XOFF`) и сначала проверять минимально рискованное изменение.

EN: Pyrometers (`app/pyrometers`, `scripts/pyrometers-setup.sh`):
- Preserve stable device naming / udev assumptions when changing setup scripts or compose wiring.
- Keep USB-path handling and low-overhead polling behavior predictable for Raspberry Pi targets.

RU: Пирометры (`app/pyrometers`, `scripts/pyrometers-setup.sh`):
- Сохранять стабильные device naming / udev-допущения при изменении setup-скриптов или compose-связности.
- Держать обработку USB-путей и low-overhead polling предсказуемыми для Raspberry Pi.

EN: Scripts (`scripts/`):
- Keep scripts idempotent and safe for repeated execution.
- Support missing optional hardware gracefully.
- Reuse shared logic in `scripts/lib/` when possible.

RU: Скрипты (`scripts/`):
- Делать скрипты идемпотентными и безопасными для повторного запуска.
- Корректно обрабатывать отсутствие опционального железа.
- По возможности переиспользовать общую логику из `scripts/lib/`.

EN: Dashboard/UI (`dashboard/simple-dash`, `app/graf`):
- Preserve low-resource friendliness and fast load behavior.
- Keep links/probes in sync with actual exposed routes.
- When frontend behavior changes after a rebuild, remember that browser hard refresh may be required to validate the real shipped assets.

RU: Dashboard/UI (`dashboard/simple-dash`, `app/graf`):
- Сохранять ориентацию на низкие ресурсы и быструю загрузку.
- Держать ссылки/probe в синхронизации с реально доступными маршрутами.
- Если поведение фронтенда меняется после пересборки, учитывать, что для проверки реально отгруженных ассетов может потребоваться hard refresh браузера.

EN: Compose/infrastructure:
- Keep service dependency intent explicit.
- Avoid introducing host-specific assumptions without feature flags or env vars.

RU: Compose/инфраструктура:
- Явно фиксировать смысл зависимостей между сервисами.
- Не добавлять host-специфичные допущения без feature-флагов или env-переменных.

EN: Matter/Thread (`scripts/openthread-host-setup.sh`, `scripts/restart-openthread.sh`, `scripts/restart-matter-server.sh`, `docs/openthread.md`):
- Both services run under Compose profiles (`thread`, `matter`) and must not affect the default stack.
- `runtime/openthread/`, `runtime/matter-server/`, and `runtime/matter-collector/` are runtime state — never commit.
- Never log or expose `ot-ctl dataset active -x` output; it contains Thread network credentials.
- `openthread-host-setup.sh --apply` is safe to rerun; it is idempotent.
- The RPi AP must have `ipv6.method link-local` (set by `rpi-nm-ap.sh`) for Matter/Thread discovery to work.
- The collector path is `matter-server -> matter-collector -> InfluxDB`.

RU: Matter/Thread (`scripts/openthread-host-setup.sh`, `scripts/restart-openthread.sh`, `scripts/restart-matter-server.sh`, `docs/openthread.md`):
- Оба сервиса работают под Compose-профилями (`thread`, `matter`) и не должны влиять на дефолтный стек.
- `runtime/openthread/`, `runtime/matter-server/` и `runtime/matter-collector/` — runtime-состояние, никогда не коммитить.
- Никогда не логировать и не раскрывать вывод `ot-ctl dataset active -x` — он содержит учётные данные Thread-сети.
- `openthread-host-setup.sh --apply` безопасен для повторного запуска, идемпотентен.
- AP на RPi должен иметь `ipv6.method link-local` (устанавливается `rpi-nm-ap.sh`) для обнаружения Matter/Thread.
- Путь коллектора: `matter-server -> matter-collector -> InfluxDB`.

## Definition of done / Критерии готовности

- EN Code builds/runs for the touched area. / RU Код собирается и запускается в затронутой зоне.
- EN Relevant tests pass (or limitations are clearly stated). / RU Релевантные тесты проходят (или ограничения явно описаны).
- EN Docs/config/templates updated if behavior or setup changed. / RU Документация/конфиги/шаблоны обновлены при изменении поведения или установки.
- EN No runtime secrets/state were added to tracked files. / RU Runtime-секреты и состояние не попали в отслеживаемые файлы.
- EN Diff is focused and reviewable. / RU Diff сфокусирован и удобен для ревью.

## References / Ссылки

- `README.md` — primary product documentation / основная документация по продукту.
- `CLAUDE.md` — assistant context and architecture summary / контекст ассистента и сводка архитектуры.
