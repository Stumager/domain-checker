# Полный гайд по backend и логике работы DNS Checker

> Важно: это исторический обзор архитектуры. Актуальным runtime-источником истины сейчас являются
> `backend/README.md`, `backend/app/`, `backend/templates/` и `backend/static/`.

Этот файл объясняет, как реально работает проект в `checker/backend`: что запускается, какие части участвуют в проверке доменов, как устроен Web Archive/Wayback-блок, какие файлы являются runtime-кодом, а какие нет.

## 1. Что здесь вообще является "рабочим кодом"

Главные runtime-файлы:

- `checker/backend/run.py` — точка входа приложения.
- `checker/backend/config.py` — все настройки через env-переменные.
- `checker/backend/app/__init__.py` — Flask app factory, сборка приложения.
- `checker/backend/app/models.py` — общее состояние текущей проверки.
- `checker/backend/app/routes.py` — почти вся orchestration-логика и API.
- `checker/backend/app/services/dns_checker.py` — быстрая DNS-проверка.
- `checker/backend/app/services/rdap_service.py` — финальная проверка через RDAP/WHOIS.
- `checker/backend/app/services/domain_processor.py` — размножение label -> label.tld.
- `checker/backend/app/utils/*.py` — нормализация, валидация, мелкие helper-функции.
- `checker/backend/templates/index.html` — HTML интерфейса.
- `checker/backend/static/js/app.js` — клиентская логика UI.

Не являются основной runtime-логикой:

- `checker/backend/build/`, `checker/backend/dist/` — артефакты сборки PyInstaller.
- `checker/backend/__pycache__/`, `checker/backend/app/**/__pycache__/` — bytecode Python.
- `checker/frontend/` — отдельная/старшая копия фронтенда, но Flask сейчас обслуживает именно `backend/templates` и `backend/static`.
- `D:\WebProjects\pod_chekcer\scripts\*.py` — отдельные утилиты, не вызываются приложением автоматически.

## 2. Самая короткая ментальная модель

Программа делает две большие вещи:

1. Проверяет список доменов на доступность:
   - сначала быстро через DNS;
   - потом подтверждает кандидатов через RDAP;
   - результаты держит в общей памяти процесса (`CheckerState`);
   - фронтенд просто опрашивает `/api/status`.

2. Показывает историю домена в Wayback Machine:
   - тянет CDX-список снимков;
   - восстанавливает redirect-цели;
   - пытается определить spam/parking/topic shift/language shift/cloaking;
   - добавляет репутационные сигналы и риск-скор.

## 3. Полный поток запуска программы

### 3.1. Запуск

Обычный запуск:

- `run.bat` ставит зависимости и вызывает `python run.py`.
- `run.py` берет конфиг через `get_config()` из `config.py`.
- `run.py` вызывает `create_app()` из `app/__init__.py`.

### 3.2. Что делает `create_app()`

Файл: `checker/backend/app/__init__.py`

`create_app()`:

- определяет, где лежат `templates/` и `static/`;
- создает Flask-приложение;
- включает `CORS(app)`;
- загружает конфиг либо из класса, либо из dict;
- собирает кусок RDAP-настроек и вручную передает их в `services.rdap_service.set_config(...)`;
- импортирует и регистрирует два blueprint:
  - `web_bp` для HTML;
  - `api_bp` для JSON API;
- создает один общий объект `CheckerState()` и кладет его в `app.checker_state`.

Ключевой смысл: состояние проверки хранится не в БД, а просто в памяти процесса.

### 3.3. Что делает `run.py`

Файл: `checker/backend/run.py`

`run.py`:

- создает приложение;
- создает `BrowserMonitor`;
- добавляет два runtime-route:
  - `/api/ping`
  - `/api/browser-disconnect`
- запускает watchdog-поток;
- через `Timer` автоматически открывает браузер;
- запускает Flask server через `app.run(...)`.

`BrowserMonitor` нужен не для проверки доменов, а чтобы exe/локальный сервер сам завершался, когда пользователь закрыл браузер.

Логика `BrowserMonitor`:

- браузер шлет heartbeat на `/api/ping`;
- при закрытии страницы шлет `/api/browser-disconnect`;
- если активных browser-session не осталось и прошла grace-задержка, процесс убивается через `os._exit(0)`.

Это удобно для desktop-like поведения, но важно помнить: завершение жесткое.

## 4. Как устроено состояние проверки

Файл: `checker/backend/app/models.py`

Класс `CheckerState` — это центральный контейнер текущего job-состояния.

Поля:

- `running` — идет ли проверка.
- `total` / `checked` — прогресс DNS-фазы.
- `stage` — `idle | dns | final | done | error`.
- `final_total` / `final_checked` / `final_errors` — прогресс RDAP-фазы.
- `available`, `taken`, `invalid`, `errors` — итоговые списки доменов.
- `current_domain` — какой домен сейчас обрабатывается.
- `message` — человекочитаемый статус.
- `lock` — защита от гонок между потоками.

`to_dict()`:

- берет lock;
- считает `progress_pct`;
- в stage `final` и `done` считает прогресс по DNS+RDAP вместе.

`reset()` возвращает все поля в исходное состояние.

## 5. Утилиты: нормализация и базовые helpers

### 5.1. `validators.py`

Файл: `checker/backend/app/utils/validators.py`

`normalize_domain(d)`:

- trim + lowercase;
- вырезает `http://` и `https://`;
- убирает path/query/fragment;
- срезает финальные `.` и `/`.

То есть если пользователь вставит URL, функция старается оставить только hostname.

`to_ascii(domain)`:

- сначала нормализует домен;
- потом кодирует его в IDNA (`xn--...`) для корректной работы с IDN.

`is_valid_domain(domain)`:

- запрещает `.gov`;
- проверяет общую длину <= 253;
- требует хотя бы одну точку;
- валидирует каждый label:
  - 1..63 символов,
  - только alnum или `-`,
  - дефис не может быть первым/последним.

### 5.2. `helpers.py`

Файл: `checker/backend/app/utils/helpers.py`

`dedupe(lst)`:

- удаляет дубликаты, сохраняя порядок.
- реализовано через `dict.fromkeys(...)`.

`parse_tlds(raw)`:

- принимает строку вида `.com, net; org`;
- режет по запятым/пробелам/`;`;
- убирает начальные точки;
- приводит к lowercase;
- делает dedupe.

`filter_domains_by_tlds(...)`:

- отбрасывает домены с нежелательными TLD/suffix.
- В текущем backend runtime почти не используется; это helper на будущее/для побочной логики.

## 6. Размножение label в домены

Файл: `checker/backend/app/services/domain_processor.py`

`expand_domains(lines, tlds)` работает так:

- если строка уже содержит `.`, считается готовым доменом;
- если точки нет, считается label;
- label размножается в `label.<tld>` для каждого TLD из списка;
- результат dedupe-ится.

Пример:

- `brand` + `["es", "it"]` -> `brand.es`, `brand.it`
- `brand.com` остается `brand.com`

Это важный момент: пользователь может вводить не только домены, но и "заготовки".

## 7. DNS-проверка: первая фаза пайплайна

Файл: `checker/backend/app/services/dns_checker.py`

### 7.1. Общая идея

DNS-фаза нужна для быстрого prefilter.

Она не пытается быть абсолютно авторитетной. Ее задача:

- быстро отделить явно занятые домены;
- выделить кандидатов для RDAP;
- пометить спорные случаи как `unknown` или `error`.

### 7.2. Режим `dnspython`

Если установлен `dnspython`, используется `_dns_check_dnspython(...)`.

Логика:

- домен нормализуется и валидируется;
- переводится в ASCII через IDNA;
- создается thread-local resolver с nameserver-ами `1.1.1.1` и `8.8.8.8`;
- сначала пробуется запрос `NS`;
- если он не дал однозначного ответа, пробуется `SOA`.

Результаты:

- `taken` — запись существует;
- `available` — `NXDOMAIN`;
- `unknown` — оба запроса дали `NoAnswer`;
- `error` — timeout / сбой / no nameservers;
- `invalid` — невалидный домен.

Почему `NS -> SOA`:

- это попытка быстро понять, делегирован ли домен в DNS;
- для availability-check этого часто хватает как дешевого первого фильтра.

### 7.3. Fallback через `socket`

Если `dnspython` не установлен, используется `_dns_check_socket(...)`.

Он:

- вызывает `socket.getaddrinfo(domain, None)`;
- если адрес найден -> `taken`;
- `gaierror` интерпретируется как `available`;
- повторяет попытки для временных ошибок.

Этот вариант грубее и менее точный.

### 7.4. Публичная точка входа

`dns_check(domain)` просто выбирает:

- `dnspython`-путь, если библиотека есть;
- иначе `socket`-fallback.

## 8. RDAP/WHOIS: вторая, более точная фаза

Файл: `checker/backend/app/services/rdap_service.py`

Это второй по важности файл проекта после `routes.py`.

### 8.1. Что хранится в глобалах

Вверху файла лежат:

- дефолтные RDAP-настройки;
- кэши bootstrap-данных;
- per-TLD semaphore-ы;
- кэш "ограниченных" TLD, где RDAP временно лучше не дергать;
- WHOIS bootstrap-cache и overrides.

Смысл:

- не парсить конфиг повторно;
- не загружать IANA bootstrap заново;
- ограничивать конкуренцию на уровне TLD;
- быстрее фейловериться на WHOIS.

### 8.2. `set_config(config_dict)`

Вызывается из `create_app()`.

Эта функция:

- копирует Flask config в глобалы `rdap_service.py`;
- сбрасывает parsed-cache;
- очищает семафоры TLD.

То есть `rdap_service.py` живет как модуль с собственным внутренним состоянием.

### 8.3. `_get_session()`

Создает thread-local `requests.Session`.

Зачем:

- переиспользование TCP-соединений;
- меньше overhead на массовых запросах;
- отдельная session на поток снижает конфликты.

### 8.4. Override-ы

Функции:

- `_parse_overrides()`
- `_parse_concurrency_overrides()`
- `_parse_whois_server_overrides()`
- `_parse_whois_not_found_overrides()`

Они парсят JSON-строки из env и делают из них словари:

- свой RDAP base URL для конкретного TLD;
- свой limit конкурентности для TLD;
- свой WHOIS server для TLD;
- свои фразы "домен не найден" для WHOIS.

### 8.5. WHOIS bootstrap

`_get_whois_server_for_tld(tld)`:

- сначала смотрит overrides;
- потом встроенные словари (`mx`, `co`);
- потом, если разрешено, спрашивает `whois.iana.org`;
- кэширует ответ.

### 8.6. Ограниченные RDAP-TLD

Функции:

- `_is_rdap_restricted(tld)`
- `_mark_rdap_restricted(tld)`

Если какой-то RDAP endpoint отдает `401/403`, TLD можно временно пометить как restricted.
Тогда следующие проверки сразу пойдут в WHOIS, не тратя время на заведомо бесполезный RDAP.

### 8.7. IANA RDAP bootstrap

`load_rdap_bootstrap()`:

- скачивает `https://data.iana.org/rdap/dns.json`;
- строит mapping `tld -> base_url`;
- кэширует его в памяти.

Если загрузка не удалась, кэшируется пустой dict.

### 8.8. Ограничение конкурентности

`_get_tld_semaphore(tld)`:

- для каждого TLD создает свой semaphore;
- лимиты берутся из:
  - JSON overrides;
  - специальных переменных для `.es` и `.it`;
  - `RDAP_CONCURRENCY_DEFAULT`.

Это очень важная защита от rate limit у регистри/реестров.

### 8.9. Повторные попытки

`_sleep_backoff(...)`:

- уважает `Retry-After`, если сервер его вернул;
- иначе делает exponential backoff + random jitter.

### 8.10. Разбор странных RDAP-ответов

Некоторые RDAP-серверы отвечают `200 OK`, но в JSON внутри лежит ошибка.

Для этого есть:

- `_rdap_hint_from_json(data)`
- `_rdap_hint_from_response(resp)`

Они пытаются понять:

- `404` в JSON -> `available`
- `400` / invalid -> `invalid`

### 8.11. `_rdap_try_get(url)`

Это основная HTTP-обертка для RDAP:

- делает GET через session;
- возвращает `(status_code, hint)`;
- ретраит `429` и `5xx`;
- может вернуть `hint`, если ошибка прочиталась из JSON-body.

### 8.12. WHOIS fallback

`_whois_query(...)` делает raw TCP-подключение на порт 43.

`_whois_check(domain_ascii, tld)`:

- получает WHOIS server;
- шлет запрос;
- по фразам в ответе решает:
  - `available`
  - `taken`
  - `error`
  - `None` если TLD не поддержан.

### 8.13. Главная функция `rdap_check(domain)`

Алгоритм:

1. Нормализует и валидирует домен.
2. Переводит в ASCII.
3. Достает TLD.
4. Если TLD уже marked restricted:
   - сразу пробует WHOIS.
5. Собирает список base URL:
   - overrides;
   - hardcoded fixes для `.es`, `.it`, `.com`;
   - данные из IANA bootstrap.
6. Если base URL нет:
   - идет в WHOIS.
7. Для каждого base URL:
   - строит `domain/<name>`;
   - делает запрос под semaphore для TLD;
   - интерпретирует результат.

Интерпретация:

- `200` -> `taken`
- `404` -> `available`
- `400` -> `invalid`
- `401/403`:
  - если включен `RDAP_FORBIDDEN_FALLBACK`, TLD временно помечается restricted и вызывается WHOIS;
  - иначе трактуется как `taken`
- сетевые/5xx/429 после всех попыток -> продолжаем на следующий base URL или уходим в WHOIS.

Если ничего не помогло, возвращается `error`.

Итого: RDAP здесь — более авторитетная проверка, а WHOIS — страховка для неудобных TLD.

## 9. `routes.py` — главный orchestration-файл

Файл: `checker/backend/app/routes.py`

Это ядро системы. В нем лежит:

- HTML route `/`;
- API `/status`, `/check`, `/download/...`, `/archive`;
- логика потоков;
- логика Wayback/CDX;
- spam/risk analysis.

Ниже разбор по блокам.

### 9.1. Базовые setup-функции

`index()`:

- просто отдает `index.html`.

`init_state()`:

- перед каждым app request один раз привязывает глобальную переменную `state` к `current_app.checker_state`.

То есть route-функции работают с одним объектом состояния, живущим в Flask app.

### 9.2. Прокси и отключение env proxy

Функции:

- `_no_env_proxies()`
- `_perform_request(...)`
- `_normalize_proxy_url(...)`
- `_mask_proxy_url(...)`
- `_proxy_kwargs(...)`
- `_build_archive_request_candidates(...)`

Они нужны только для Web Archive.

Смысл:

- пользователь может ввести proxy руками в UI;
- при direct fallback код должен уметь явно отключить системные `HTTP_PROXY/HTTPS_PROXY`;
- в UI proxy показывается без логина/пароля.

`_build_archive_request_candidates(...)` формирует порядок попыток:

- сначала пользовательский proxy;
- потом direct connection, если разрешен fallback.

### 9.3. CDX / Wayback список снимков

Функции:

- `_iter_archive_cdx_urls()`
- `_parse_cdx_page(payload)`
- `_fetch_archive_rows(...)`
- `_fmt_ts(ts)`
- `_normalize_wayback_location(location)`

Как это работает:

1. Берется CDX endpoint:
   - сначала HTTPS,
   - потом при необходимости HTTP fallback.
2. `_fetch_archive_rows(...)` постранично тянет CDX JSON.
3. Используется `resumeKey`, чтобы обходить большие выдачи.
4. Запрашиваются поля:
   - `timestamp`
   - `original`
   - `statuscode`
   - `redirect`
   - `redirecturl`
5. Данные сортируются по времени по убыванию.

Выход `_fetch_archive_rows(...)`:

- список строк `(timestamp, original, status, redirect)`;
- диапазон лет;
- `truncated`;
- был ли в выдаче redirect-column.

### 9.4. Добор redirect-целей у снимков

Функции:

- `_probe_snapshot_redirect(...)`
- `_enrich_missing_redirects(...)`

Проблема:

- CDX не всегда возвращает redirect target;
- прокси иногда ломают `Location`;
- часть снимков имеет статус `301/302`, но redirect пустой.

Решение:

- для таких строк делается легкий запрос в `https://web.archive.org/web/<ts>id_/<orig>`;
- читается `Location` header;
- если запрос через proxy не помог, возможен direct fallback;
- результаты подмешиваются обратно в rows.

### 9.5. Spam-анализ содержимого snapshot

Функции:

- `_normalize_spam_text`
- `_normalize_url_for_spam`
- `_extract_host_from_url`
- `_dominant_script`
- `_extract_link_candidates`
- `_normalize_link_text`
- `_build_spam_haystacks`
- `_count_tracking_params`
- `_analyze_snapshot_content`
- `_build_topic_signature`
- `_looks_like_keyword_stuffing`
- `_looks_like_doorway`
- `_looks_like_domain_parking`
- `_detect_spam_topics`
- `_detect_spam_from_url`
- `_fetch_snapshot_sample`
- `_probe_snapshot_spam`
- `_probe_snapshot_signature`
- `_is_spam_probe_candidate`
- `_enrich_spam_flags`

Что делает весь этот блок:

- скачивает небольшой кусок HTML снапшота;
- извлекает видимый текст;
- извлекает ссылки;
- ищет паттерны porn/casino/pharma/betting/chinese spam/doorway/parking;
- строит n-gram signature текста;
- считает метрики:
  - длина текста,
  - число ссылок,
  - доля внешних ссылок,
  - tracking params,
  - keyword stuffing,
  - thin content,
  - dominant script.

`_enrich_spam_flags(...)`:

- выбирает не все rows, а ограниченное число `max_probe`;
- параллельно пробует снапшоты;
- возвращает:
  - попадания spam по индексам,
  - сколько проверено,
  - сколько flagged,
  - topic signatures,
  - длины текстов,
  - propagated labels,
  - metrics.

`propagated labels` — важная идея:

- если подозрительных снапшотов слишком много, их spam-метки могут распространяться на весь набор.

### 9.6. Topic shift, language shift, cloaking

Функции:

- `_jaccard_similarity`
- `_detect_topic_shifts`
- `_detect_language_shifts`
- `_detect_cloaking`

Логика:

- topic shift:
  - сравниваются подписи соседних снапшотов;
  - если сходство слишком низкое, считается, что тематика резко сменилась.

- language shift:
  - определяется доминирующий script;
  - если в соседнем снапшоте script другой, ставится флаг.

- cloaking:
  - один и тот же snapshot качается как обычным UA и как bot UA;
  - если сигнатуры слишком разные, ставится флаг cloaking.

### 9.7. Репутация и внешние блоклисты

Функции:

- `_load_blocklist_file`
- `_load_blocklists`
- `_load_urlhaus_hosts`
- `_check_safe_browsing`
- `_check_phishtank`
- `_check_reputation`

Этот блок уже не про исторический контент, а про "внешние сигналы риска".

Проверки:

- Google Safe Browsing;
- PhishTank;
- локальные blocklist-файлы;
- URLhaus host list.

Все это опционально и работает только если заданы нужные настройки/API keys.

### 9.8. Возраст домена и сертификата

Функции:

- `_parse_rdap_event_date`
- `_fetch_rdap_age_days`
- `_fetch_tls_age_days`

Они добавляют еще два риска:

- домен недавно зарегистрирован;
- TLS-сертификат слишком новый.

### 9.9. Расчет risk score

Функция: `_compute_domain_risk(...)`

Она объединяет все сигналы:

- spam content;
- ideographs;
- parked;
- topic shift;
- language shift;
- cloaking;
- spam links;
- keyword stuffing;
- thin content;
- link farm;
- tracking-heavy links;
- young domain;
- young cert;
- reputation hit.

На основе этого считается:

- `score` от 0 до 100;
- `flags`;
- `not_suitable`.

`not_suitable` может включиться не только по score, но и по сильным reputation/spam/parked-сигналам.

### 9.10. Поточный helper

Функция: `_run_thread_pool(items, worker, max_workers, max_in_flight=None)`

Это обертка над `ThreadPoolExecutor`, которая:

- не закидывает весь огромный batch в executor сразу;
- ограничивает число futures "в полете";
- экономит память на больших списках.

Это полезная реализация, потому что проект может гонять десятки и сотни тысяч доменов.

## 10. Основной pipeline проверки доменов: `run_check(...)`

Файл: `checker/backend/app/routes.py`

Это главный backend-пайплайн для `/api/check`.

Шаги:

1. Ставит `state.running = True`, `stage = "dns"`.
2. Обнуляет старые результаты.
3. Параллельно прогоняет все домены через `dns_check()`.
4. В зависимости от результата раскидывает домен по спискам.
5. При желании делает RDAP recheck для `errors`.
6. Потом делает RDAP final check для `available`.
7. В конце dedupe-ит все списки и ставит `stage = "done"`.

### 10.1. Очень важная деталь: как трактуется DNS `unknown`

Внутри worker-логики:

- если TLD входит в `DNS_PREFILTER_STRICT_TLDS`, то `unknown` трактуется как `taken`;
- иначе домен одновременно добавляется:
  - в `available`
  - и в `errors`

Зачем так:

- пользователь сразу видит кандидатов;
- но система помнит, что DNS-ответ был неуверенный, и может перепроверить домен в RDAP.

Это один из самых тонких моментов проекта.

### 10.2. RDAP recheck для errors

Если включены:

- `FINAL_CHECK_ENABLED`
- `rdap_recheck_errors`

то сначала recheck-ятся домены из `state.errors`.

Результат:

- `available` -> в available
- `taken` -> в taken
- `invalid` -> в invalid
- `error` -> остается в errors, `final_errors += 1`

### 10.3. Финальный RDAP check для available

После этого:

- берутся все кандидаты из `state.available`;
- `state.available` очищается;
- каждый кандидат проходит через `rdap_check()`;
- финально в `available` попадают только те, кто подтвердился как `available`.

Очень важно:

- если RDAP дал `error`, домен идет в `errors`, но не считается available.
- это защита от ложных позитивов.

## 11. API endpoints

### 11.1. `GET /api/status`

Просто возвращает `state.to_dict()`.

Фронтенд дергает этот endpoint каждые 200 мс, пока идет проверка.

### 11.2. `POST /api/check`

Это старт новой проверки.

Что делает route:

- запрещает старт, если уже идет scan;
- читает `domains`, `threads`, `tlds`, `rdap_recheck_errors`;
- разбивает ввод на строки;
- парсит TLD;
- если TLD не пришли, берет `DEFAULT_TLDS` из конфига;
- делает `expand_domains(...)`;
- ограничивает batch через `MAX_DOMAINS` (по умолчанию 200000);
- парсит `DNS_PREFILTER_STRICT_TLDS`;
- запускает background-thread с `run_check(...)`;
- сразу возвращает JSON "started".

Важно:

- здесь нет очереди задач;
- здесь нет хранения job history;
- одновременно поддерживается один scan в одном процессе.

### 11.3. `GET /api/download/<result_type>`

Route:

- берет один из списков из `state`;
- пишет временный `.txt`;
- отдает его через `send_file(...)`.

Типы:

- `available`
- `taken`
- `invalid`
- `errors`

### 11.4. `POST /api/archive`

Это отдельный мини-пайплайн Wayback.

Что делает route:

1. Нормализует домен.
2. Читает proxy из запроса.
3. Формирует `headers` и proxy-state для UI.
4. Пробует комбинации:
   - proxy/direct;
   - HTTPS CDX / HTTP CDX.
5. Через `_fetch_archive_rows(...)` вытягивает список snapshot rows.
6. Если нужно, восстанавливает redirect-цели.
7. Запускает spam/topic/lang/cloaking enrichment.
8. Считает URL-level spam.
9. Формирует `results` для таблицы.
10. Параллельно/последовательно добирает:
    - reputation,
    - RDAP age,
    - TLS age.
11. Считает `risk`.
12. Возвращает большой JSON для modal-таблицы.

Именно здесь живет "Web Archive гарантированно".

## 12. Как фронтенд реально общается с backend

### 12.1. `index.html`

Файл: `checker/backend/templates/index.html`

В HTML есть:

- textarea для доменов;
- input для threads;
- checkbox `RDAP recheck for Errors`;
- кнопки `Start`, `Stop`, `Web Archive`;
- progress-блок;
- result cards;
- modal для архива.

### 12.2. `static/js/app.js`

Фронтенд логика полностью на vanilla JS.

Основные блоки:

- browser heartbeat:
  - `ensureBrowserSessionId`
  - `pingServer`
  - `disconnectServer`

- запуск проверки:
  - `startCheck()`

- polling статуса:
  - `updateStatus()`

- загрузка результатов:
  - `downloadResult()`
  - `downloadAllResults()`

- работа с Wayback modal:
  - `toggleArchiveModal()`
  - `fetchWaybackData()`
  - `buildArchiveRowHtml()`
  - `updateArchiveMeta()`

- обработка файлов и drag&drop:
  - `parseDomainsFromText()`
  - `appendDomainsToTextarea()`
  - `loadDomainsFromFiles()`
  - drag/drop handlers

- TLD-filter:
  - `parseExtraTldAllowList()`
  - `shouldKeepDomainForTldFilter()`
  - `filterTlds()`

### 12.3. Поток UI для обычной проверки

1. Пользователь жмет `Start Check`.
2. `startCheck()` шлет `POST /api/check`.
3. Если ok, JS начинает `setInterval(updateStatus, 200)`.
4. `updateStatus()` обновляет:
   - counters;
   - progress bar;
   - current domain;
   - stage/message.
5. Когда `running == false`, UI:
   - скрывает progress;
   - показывает result cards;
   - включает download-кнопки.

### 12.4. Поток UI для Web Archive

1. Пользователь открывает modal.
2. Вводит domain и optional proxy.
3. `fetchWaybackData()` шлет `POST /api/archive`.
4. Backend возвращает rows + meta + risk.
5. `updateArchiveMeta()` заполняет:
   - диапазон лет;
   - какой connection реально использовался;
   - какой CDX endpoint реально использовался;
   - total snapshots;
   - risk score.
6. `renderArchiveRowsChunked()` рендерит большую таблицу кусками, чтобы не заморозить UI.

## 13. Настройки в `config.py`

Файл: `checker/backend/config.py`

Этот файл определяет почти все tunables проекта.

Ключевые группы:

- Flask:
  - `DEBUG`, `HOST`, `PORT`

- RDAP:
  - `RDAP_BOOTSTRAP_URL`
  - `FINAL_CHECK_ENABLED`
  - `FINAL_CHECK_WORKERS`
  - `RDAP_TIMEOUT`
  - retry/backoff
  - concurrency overrides
  - session pooling
  - WHOIS fallback tuning

- Расширение label:
  - `DEFAULT_TLDS`
  - `DNS_PREFILTER_STRICT_TLDS`

- Wayback:
  - диапазон лет;
  - timeout/retries;
  - max pages / rows / seconds;
  - proxy/direct fallback;
  - redirect probing;
  - spam/topic/lang/cloak analysis;
  - reputation;
  - RDAP age / TLS age;
  - threshold для `not_suitable`.

По сути `config.py` решает, насколько агрессивно и глубоко работает система.

## 14. Отдельные utility scripts вне `checker/backend`

Путь: `D:\WebProjects\pod_chekcer\scripts\`

Они не вызываются backend-ом автоматически.

Что они делают:

- `check_redirects.py` — маленький standalone HTTP redirect checker.
- `find_inter_duplicates.py` — ищет пересечения доменов между файлами.
- `wayback_snapshots.py` — отдельный консольный Wayback lookup.

Их роль:

- вспомогательные ручные утилиты;
- не часть веб-приложения runtime.

## 15. Важные практические нюансы и подводные камни

### 15.1. Кнопка `Stop` не останавливает backend job

Во фронтенде `stopCheck()`:

- скрывает/показывает кнопки;
- выключает polling.

Но:

- route `/api/stop` нет;
- cancel-flag в `run_check()` нет.

То есть кнопка стопает только UI-обновление, а не сам scan.

### 15.2. Один процесс = одно состояние

Так как используется один `CheckerState` в памяти:

- параллельно держать несколько jobs нельзя;
- при рестарте процесса история теряется.

### 15.3. `frontend/` папка сейчас не главный источник UI

Текущий Flask runtime обслуживает:

- `backend/templates`
- `backend/static`

Если ты меняешь `checker/frontend/...`, это не гарантирует изменения в рабочем интерфейсе.

### 15.4. Есть мелкие следы старых версий UI

Например:

- в JS есть ссылка на `archiveRedirectInfo`, но такого элемента нет в текущем `index.html`;
- README местами описывает элементы, которых уже нет или они работают чуть иначе.

Это не ломает основной runtime, но полезно помнить при доработках.

### 15.5. `MAX_DOMAINS` читается, но явно не объявлен в `config.py`

В `/api/check` он берется через:

- `current_app.config.get("MAX_DOMAINS", "200000")`

То есть:

- настройка поддержана;
- но в `config.py` ее отдельной строкой сейчас нет.

### 15.6. Память и сеть важнее CPU

Проект в основном упирается в:

- DNS latency;
- RDAP rate limits;
- Wayback latency;
- HTTP retries;
- количество snapshot-проб.

То есть увеличение threads/workers не всегда ускоряет, а иногда ухудшает ситуацию.

## 16. С чего тебе лучше читать код по порядку

Если цель — реально начать уверенно менять проект, я бы советовал такой порядок:

1. `checker/backend/run.py`
2. `checker/backend/app/__init__.py`
3. `checker/backend/app/models.py`
4. `checker/backend/app/utils/validators.py`
5. `checker/backend/app/services/dns_checker.py`
6. `checker/backend/app/services/domain_processor.py`
7. `checker/backend/app/services/rdap_service.py`
8. `checker/backend/app/routes.py`
9. `checker/backend/templates/index.html`
10. `checker/backend/static/js/app.js`
11. только потом `config.py` перечитывать второй раз уже с пониманием потока

Почему именно так:

- сначала поймешь запуск и состояние;
- потом базовые проверки;
- потом тяжелую orchestration-логику;
- потом UI.

## 17. Самая важная схема в одном блоке

Обычная проверка доменов:

1. UI -> `POST /api/check`
2. backend -> `expand_domains(...)`
3. background thread -> `run_check(...)`
4. DNS prefilter -> `dns_check(...)`
5. optional RDAP error recheck -> `rdap_check(...)`
6. final RDAP verification -> `rdap_check(...)`
7. state обновляется
8. UI опрашивает `GET /api/status`
9. download идет через `GET /api/download/<type>`

Wayback / Web Archive:

1. UI modal -> `POST /api/archive`
2. backend -> CDX fetch
3. optional proxy/direct fallback
4. redirect enrichment
5. spam/topic/lang/cloaking enrichment
6. reputation + age signals
7. risk scoring
8. UI рендерит таблицу snapshot-ов

## 18. Что здесь менять, если тебе нужны конкретные доработки

Если нужно:

- поменять правила валидации домена -> `validators.py`
- поменять DNS-логику -> `dns_checker.py`
- поменять финальную availability-логику -> `rdap_service.py` и `run_check(...)`
- поменять API обычной проверки -> `routes.py` (`/api/check`, `/api/status`, `/api/download`)
- поменять Wayback/архив -> `routes.py` (`/api/archive` и helper-блоки вокруг него)
- поменять UI обычной проверки -> `templates/index.html` + `static/js/app.js`
- поменять risk scoring -> `_compute_domain_risk(...)` в `routes.py`
- поменять конфигурацию/таймауты/лимиты -> `config.py`

## 19. Итог

Если очень коротко:

- `run.py` запускает приложение и lifecycle браузера;
- `create_app()` собирает Flask и подключает state + routes;
- `run_check()` — сердце обычной проверки доменов;
- `dns_checker.py` — быстрый prefilter;
- `rdap_service.py` — более точная финальная проверка;
- `routes.py` — основной orchestration и весь Web Archive;
- `app.js` — тонкий клиент, который вызывает API и показывает результат.

Для практической работы тебе в первую очередь надо уверенно понимать:

- `run_check(...)`
- `rdap_check(...)`
- `/api/check`
- `/api/archive`
- `updateStatus()` и `fetchWaybackData()`

Именно эти места задают почти все пользовательское поведение приложения.
