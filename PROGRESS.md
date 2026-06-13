# Project Audit & Cleanup Progress

## Stage 1: Audit — COMPLETE

---

## What the project does

**DNS Checker** — локальное Flask-приложение для проверки доступности доменных имён.

Пайплайн работы:
1. Принимает список доменов или меток (без точки)
2. Расширяет метки по настроенным TLD (es, it, fr, de, pl…)
3. DNS prefilter — массовая проверка через dnspython (NS/SOA записи), определяет taken/available/unknown
4. RDAP final check — уточнение "unknown" и "available" через RDAP API с WHOIS-fallback
5. Wayback Machine archive — по запросу смотрит историю домена: спам-контент, парковка, смена темы, языковые сдвиги, клоакинг, репутация (Safe Browsing, PhishTank)
6. Автоматически открывает браузер и закрывается при закрытии вкладки
7. Экспорт результатов в TXT и ZIP

---

## Файлы проекта

| Файл | Описание |
|------|----------|
| `backend/app/__init__.py` | Фабрика Flask-приложения: CORS, состояние, monitor, RDAP-конфиг |
| `backend/app/browser_monitor.py` | Следит за heartbeat браузера, завершает процесс при закрытии вкладки |
| `backend/app/models.py` | `CheckerState` — thread-safe состояние текущего скана |
| `backend/app/routes.py` | **2185 строк** — все API-роуты + Wayback-интеграция + спам-детект + анализ контента |
| `backend/app/services/dns_checker.py` | DNS-проверка через dnspython (fallback: socket) |
| `backend/app/services/domain_processor.py` | Расширение меток по TLD-списку |
| `backend/app/services/rdap_service.py` | RDAP-запросы с WHOIS-fallback для TLD без RDAP |
| `backend/app/utils/helpers.py` | dedupe, parse_tlds, filter_domains_by_tlds |
| `backend/app/utils/validators.py` | normalize_domain, to_ascii, is_valid_domain |
| `backend/config.py` | Вся конфигурация через os.getenv() — чисто |
| `backend/run.py` | Точка входа |
| `backend/run.bat` | Установка зависимостей и запуск на Windows |
| `backend/rebuild.bat` | Сборка exe через PyInstaller + создание ярлыка |
| `backend/build.py` | Скрипт сборки PyInstaller |
| `backend/DNS_Checker.spec` | Спецификация PyInstaller |
| `backend/templates/index.html` | Главная HTML-страница |
| `backend/static/css/style.css` | Стили |
| `backend/static/js/app.js` | Frontend JavaScript |
| `backend/tests/test_app.py` | 5 регрессионных тестов |
| `backend/.env.example` | Пример конфига без секретов |
| `backend/requirements.txt` | Зависимости (устаревшие версии 2023 года) |
| `backend/icon.ico` | Иконка приложения |
| `backend/available.txt` | ⚠️ Результаты реального скана с именами доменов |
| `backend/build/` | ⚠️ Артефакты PyInstaller (бинарники) |
| `backend/dist/DNS_Checker.exe` | ⚠️ Скомпилированный exe (~30-50 MB) |
| `backend/DNS_Checker.lnk` | ⚠️ Ярлык Windows |
| `backend/__pycache__/` (везде) | ⚠️ Скомпилированный Python-байткод |
| `frontend/README.md` | Заглушка (старый frontend удалён, файл не нужен) |
| `scripts/aggregate_csv.py` | Утилита: агрегация CSV с доменами |
| `scripts/check_redirects.py` | Утилита: проверка редиректов |
| `scripts/find_duplicates.py` | Утилита: поиск дублей внутри файла |
| `scripts/find_inter_duplicates.py` | Утилита: поиск дублей между файлами |
| `scripts/wayback_snapshots.py` | Утилита: получение снапшотов Wayback |
| `scripts/__pycache__/` | ⚠️ Байткод скриптов |
| `.gitignore` | Есть, но **неполный** (не исключает build/, dist/, *.lnk, *.exe) |
| `README.md` | Базовая документация (минимальная) |

---

## Топ-5 серьёзных проблем

### 🔴 #1 — Бинарные артефакты и байткод в git

В репозитории закоммичены файлы, которые **не должны быть в GitHub**:

- `backend/dist/DNS_Checker.exe` — скомпилированный Windows-бинарник (~30–50 MB), раздует репозиторий
- `backend/build/` — промежуточные артефакты PyInstaller (бинарные файлы .pkg, .pyz, .zip)
- `backend/DNS_Checker.lnk` — Windows-ярлык, платформозависим и бесполезен в GitHub
- `backend/__pycache__/` и `scripts/__pycache__/` — Python-байткод (несмотря на запись в .gitignore, похоже были добавлены до него)

**Почему это проблема**: Репозиторий станет огромным, бинарники изменяются при каждой сборке, засоряют историю.

---

### 🔴 #2 — Закоммиченные результаты реального скана

`backend/available.txt` содержит реальные доменные имена из рабочего скана:
```
arab-post.com
andalucia-andalusia.com
anyiptvplayer.com
...
```

**Почему это проблема**: Раскрывает бизнес-интерес к конкретным доменам, возможно конфиденциальную рабочую информацию.

---

### 🟡 #3 — God File: routes.py (2185 строк)

`backend/app/routes.py` делает слишком много в одном файле:
- API-роуты (`/check`, `/status`, `/stop`, `/download-*`, `/archive`)
- Полная интеграция с Wayback Machine CDX API (пагинация, таймауты, ретраи)
- Детекция спама (300+ паттернов regex для porn/casino/pharma/betting/parked)
- Анализ контента снапшотов (topic shift, language shift, cloaking)
- Reputation checks (Safe Browsing, PhishTank, URLhaus, blocklists)
- TLS-сертификат age check

**Почему это проблема**: HR-рецензент откроет файл и увидит 2185 строк — производит впечатление хаоса, хотя логика сама по себе корректна.

---

### 🟡 #4 — Устаревшие зависимости

`requirements.txt` зафиксирован на версиях 2023 года:
```
Flask==2.3.3          # актуально: 3.1.x
requests==2.31.0      # актуально: 2.32.x
dnspython==2.4.2      # актуально: 2.7.x
psutil==5.9.6         # актуально: 6.1.x
```

**Почему это проблема**: Устаревшие зависимости сигнализируют о небрежности; Flask 2.3.x имел ряд исправлений безопасности.

---

### 🟡 #5 — Нет SECRET_KEY + DEBUG=True по умолчанию

В `config.py`:
- `DevelopmentConfig.DEBUG = True` — активирован по умолчанию, в браузере будут видны трейсбеки с системными путями
- Не задан `SECRET_KEY` — Flask предупреждает об этом при запуске
- `ARCHIVE_YEAR_TO = 2026` захардкожен в дефолтах — нужно обновлять ежегодно

**Почему это проблема**: Для HR-проекта это производит впечатление незавершённости. `DEBUG=True` в продакшн-конфиге — классическая ошибка новичка.

---

## Дополнительные наблюдения (не критично)

- `frontend/README.md` — бессмысленный пустой каталог с одним файлом, создаёт путаницу
- `scripts/` не имеет README (непонятно что это и как запускать)
- `import traceback` внутри except-блока в `routes.py:1826` — должен быть в топ импортах
- `_URLHAUS_CACHE`, `_BLOCKLIST_CACHE` — mutable globals в routes.py (работает, но нарушает принцип чистоты)
- Нет `LICENSE` файла — обязательно для открытого GitHub-проекта
- Нет скриншота/демо-гифки — снижает первое впечатление на GitHub

---

## Секреты и API-ключи

**Захардкоженных секретов НЕ найдено.**

- Google Safe Browsing API key — только через `os.getenv("ARCHIVE_REPUTATION_SAFE_BROWSING_KEY", "")`
- PhishTank key — только через `os.getenv("ARCHIVE_REPUTATION_PHISHTANK_KEY", "")`
- `.env.example` содержит только безопасные дефолты
- `.env` правильно исключён в `.gitignore`

---

## План следующих этапов

- [ ] **Этап 2**: Рефакторинг и фикс багов
  - [ ] Разбить routes.py на модули (archive_routes.py, spam_detector.py)
  - [ ] Вынести traceback import наверх
  - [ ] Обновить requirements.txt
  - [ ] Добавить SECRET_KEY в конфиг
  - [ ] Убрать пустой `frontend/`
- [ ] **Этап 3**: Визуальные исправления
- [ ] **Этап 4**: Безопасность — удалить артефакты и закоммиченные данные
  - [ ] Удалить dist/, build/, *.lnk, __pycache__/ из истории git
  - [ ] Удалить available.txt из истории git
  - [ ] Обновить .gitignore
- [ ] **Этап 5**: README, LICENSE, .gitignore финальный
