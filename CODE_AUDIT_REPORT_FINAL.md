# Отчёт аудита кода — SKLAD_0.2

**Файл:** `app.py` + шаблоны, `static/`, `sw.js`, `manifest.json`  
**Дата аудита:** 2026-03-13  
**Версия проекта:** 0.2  

---

## 📊 Сводная статистика

| Уровень | Количество |
|---------|-----------|
| 🔴 Критический | 5 |
| 🟠 Высокий | 10 |
| 🟡 Средний | 11 |
| 🔵 Низкий | 6 |
| **ИТОГО** | **32** |

---

## 🗂 Быстрый план действий

### 🔴 Исправить немедленно (до любого деплоя)
1. Нет транзакционной защиты в `create_operation` / `update_operation`
2. Двойное применение инвентаризации (`inventory_session` + `inventory_complete`)
3. `fifo_remove_lot` не корректирует `lot_consumptions`
4. `create_operation` не валидирует `op_type`
5. OUT-операция допускает отрицательный остаток

### 🟠 Исправить до релиза (эта неделя)
6. `inject_globals` открывает новые соединения с БД на каждый запрос
7. Хрупкая текстовая замена в SQL (`stats_detail`)
8. Непоследовательная политика паролей
9. Отсутствие rate limiting на `/login`
10. Timezone не валидируется при сохранении
11. N+1 запрос в истории операций
12. Отсутствуют индексы на ключевых полях
13. Отсутствие security headers
14. Небезопасная загрузка файлов (нет проверки размера и MIME)
15. `inventory_complete` не проверяет права доступа

### 🟡 Исправить в текущем спринте
16. `local_dt_filter` делает SELECT на каждое отображение даты
17. `debug=True` захардкожен в `__main__`
18. Внутренний `from datetime import datetime` в `stock_print`
19. `history` принимает `page` без валидации
20. `reorder_products` не валидирует id продуктов
21. `init_db` не закрывает соединение при исключении
22. Публичный доступ к `/uploads/<path>` без ограничений
23. Race condition в `inventory_complete`
24. Отсутствие `try/except` на fetch-запросах в JS
25. Нет обработчиков 404/500 ошибок
26. `debug=True` при старте через `python app.py`

### 🔵 По возможности
27. Лишний импорт `datetime` в `calc_danger_thresholds`
28. Отсутствует индекс `lots(product_id, remaining_qty)`
29. `page <= 0` не обрабатывается в `history`
30. Отсутствует конфигурация логирования
31. Нет единого места для констант (разбросаны по файлу)
32. Health check endpoint

---

## 📋 Детальный отчёт

---

## 🔴 КРИТИЧЕСКИЕ

---

### [CRITICAL-1] Нет транзакционной защиты в `create_operation` и `update_operation`

**Расположение:** `app.py:~630–680`, `~700–770`

**Проблема:**
Операция записывает в несколько таблиц (`operations`, `operation_items`, `lots`, `lot_consumptions`) и обновляет `products.current_stock` без явной транзакции с rollback. При исключении в середине цикла (например, `fifo_consume` упал) БД остаётся в частично изменённом состоянии.

**Импакт:** Повреждение остатков — `current_stock` уедет, а `lot_consumptions` не запишутся. Данные склада становятся недостоверными.

**Исправление:**
```python
def create_operation():
    db = get_db()
    try:
        db.execute("BEGIN")
        # ... вся логика создания операции ...
        db.execute("COMMIT")
    except Exception as e:
        db.execute("ROLLBACK")
        logger.error("create_operation failed: %s", e)
        flash('Ошибка при создании операции. Изменения отменены.')
        return redirect(url_for('new_operation'))
```

---

### [CRITICAL-2] Двойное применение корректировок инвентаризации

**Расположение:** `app.py:~1020–1060` и `app.py:~1080–1120`

**Проблема:**
При `save_action="complete"` метод `inventory_session` сам применяет корректировки (`INSERT INTO operations`, `UPDATE products`). Но также существует отдельный роут `inventory_complete`, который делает то же самое. Если форма или JS вызовут оба — корректировки применятся дважды.

**Импакт:** Двойное изменение остатков, дублирование ADJUST-операций, порча финансовой статистики.

**Исправление:** Убрать логику завершения из `inventory_session` и редиректить на `inventory_complete`, либо полностью удалить `inventory_complete` как дублирующий роут.

---

### [CRITICAL-3] `fifo_remove_lot` не восстанавливает `lot_consumptions`

**Расположение:** `app.py:~530–540`

**Проблема:**
При удалении IN-операции `fifo_remove_lot` обнуляет `remaining_qty` у лота если `consumed > 0`, но не удаляет и не корректирует записи `lot_consumptions`, ссылающиеся на этот лот. При последующих FIFO-расчётах старые потребления «висят» в таблице.

**Импакт:** Некорректный расчёт себестоимости FIFO, phantom consumptions.

**Исправление:**
```python
def fifo_remove_lot(db, operation_item_id):
    lot = db.execute("SELECT * FROM lots WHERE operation_item_id=?", (operation_item_id,)).fetchone()
    if lot and lot['consumed'] > 0:
        # Удалить связанные потребления
        db.execute("DELETE FROM lot_consumptions WHERE lot_id=?", (lot['id'],))
    db.execute("DELETE FROM lots WHERE operation_item_id=?", (operation_item_id,))
```

---

### [CRITICAL-4] `create_operation` не валидирует `op_type`

**Расположение:** `app.py:~611`

**Проблема:**
`op_type = request.form.get("type", "IN")` принимает любую строку. БД имеет CHECK `IN/OUT/ADJUST`, но в коде нет проверки до INSERT. При неожиданном значении — необработанный `sqlite3.IntegrityError`.

**Исправление:**
```python
op_type = request.form.get("type", "IN")
if op_type not in ("IN", "OUT", "ADJUST"):
    abort(400)
```

---

### [CRITICAL-5] OUT-операция допускает отрицательный остаток

**Расположение:** `app.py:~660`, `~760`

**Проблема:**
OUT-операция не проверяет `current_stock >= qty` перед `UPDATE products SET current_stock = current_stock - ?`.

**Импакт:** Отрицательные остатки, некорректный FIFO (`fifo_consume` вернёт `None`).

**Исправление:**
```python
row = db.execute("SELECT current_stock FROM products WHERE id=?", (pid,)).fetchone()
if row['current_stock'] < qty:
    flash(f'Недостаточно товара на складе (доступно: {row["current_stock"]})')
    return redirect(url_for('new_operation'))
db.execute("UPDATE products SET current_stock = current_stock - ? WHERE id=?", (qty, pid))
```

---

## 🟠 ВЫСОКИЕ

---

### [HIGH-1] `inject_globals` открывает новые соединения с БД на каждый запрос

**Расположение:** `app.py:~240–248`

**Проблема:**
`get_setting` внутри `inject_globals` использует `sqlite3.connect(DB_PATH)` (не `get_db()`), открывая отдельное соединение для каждого из 3–4 вызовов при каждом рендеринге шаблона.

**Исправление:** Переписать `inject_globals` для использования `get_db()` из контекста запроса.

---

### [HIGH-2] `local_dt_filter` делает SELECT на каждое отображение даты

**Расположение:** `app.py:~220–232`

**Проблема:**
Фильтр `localdt` запрашивает `get_setting('timezone')` при каждом вызове. На странице истории — 20+ записей → 20+ дополнительных SELECT.

**Исправление:**
```python
@app.template_filter('localdt')
def local_dt_filter(value, fmt='%d.%m.%Y %H:%M'):
    if not value:
        return '—'
    if 'cached_tz' not in g:
        tz_name = get_setting('timezone', 'UTC')
        try:
            g.cached_tz = ZoneInfo(tz_name)
        except ZoneInfoNotFoundError:
            g.cached_tz = ZoneInfo('UTC')
    # ... использовать g.cached_tz
```

---

### [HIGH-3] Хрупкая текстовая замена строк в SQL (`stats_detail`)

**Расположение:** `app.py:~840–865`

**Проблема:**
`prod_filter.replace('AND oi.product_id','AND p.id')` — ненадёжная текстовая замена. Параметры `prod_params` передаются трижды, что легко нарушить при рефакторинге.

**Импакт:** Неправильный порядок параметров → некорректные результаты или `sqlite3.ProgrammingError`.

**Исправление:** Переписать запрос без текстовой замены, явно указав нужные условия в зависимости от типа субъекта.

---

### [HIGH-4] Непоследовательная политика паролей

**Расположение:** `app.py:~420`, `~setup`

**Проблема:**
`change_password` принимает пароль от 4 символов. `setup` требует 12+ символов с mixed case, цифрами и спецсимволами.

**Исправление:**
```python
def validate_password(password):
    if len(password) < 8:
        return 'Минимум 8 символов'
    if not re.search(r'[A-Za-z]', password):
        return 'Хотя бы одна буква'
    if not re.search(r'\d', password):
        return 'Хотя бы одна цифра'
    return None

# Применять во всех местах смены/создания пароля
```

---

### [HIGH-5] Отсутствие rate limiting на `/login`

**Расположение:** `app.py:~334–356`

**Проблема:**
Эндпоинт не имеет ограничения попыток входа — возможен перебор паролей.

**Исправление:**
```python
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

limiter = Limiter(app, key_func=get_remote_address)

@app.route('/login', methods=['GET', 'POST'])
@limiter.limit("10 per minute")
def login():
    ...
```

---

### [HIGH-6] Timezone не валидируется при сохранении

**Расположение:** `app.py:~320`

**Проблема:**
`tz = request.form.get('timezone', 'UTC')` сохраняется без проверки. Произвольная строка вызовет `ZoneInfoNotFoundError` при следующем рендеринге.

**Исправление:**
```python
tz = request.form.get('timezone', 'UTC')
try:
    ZoneInfo(tz)  # Проверка валидности
except ZoneInfoNotFoundError:
    flash('Неизвестный часовой пояс')
    return redirect(url_for('settings'))
```

---

### [HIGH-7] N+1 запрос в истории операций

**Расположение:** `app.py:~1009–1018`

**Проблема:**
Для каждой операции выполняется отдельный запрос к `operation_items`. При 100 операциях — 101 запрос к БД.

**Исправление:** Загрузить все items одним запросом и сгруппировать в Python:
```python
op_ids = [op['id'] for op in ops]
placeholders = ','.join('?' * len(op_ids))
all_items = db.execute(f"""
    SELECT oi.*, p.name as product_name, u.short_name as unit_short
    FROM operation_items oi
    JOIN products p ON p.id = oi.product_id
    LEFT JOIN units u ON u.id = p.unit_id
    WHERE oi.operation_id IN ({placeholders})
""", op_ids).fetchall()

items_by_op = {}
for item in all_items:
    items_by_op.setdefault(item['operation_id'], []).append(item)
```

---

### [HIGH-8] Отсутствуют индексы на ключевых полях

**Расположение:** `app.py:~197–212` (блок `init_db`)

**Проблема:**
Нет индексов для полей, активно используемых в JOIN и WHERE:
- `operation_items.operation_id`
- `operation_items.product_id`
- `lots.product_id`
- `lots.remaining_qty`

**Исправление:**
```python
db.execute("CREATE INDEX IF NOT EXISTS idx_op_items_op_id ON operation_items(operation_id)")
db.execute("CREATE INDEX IF NOT EXISTS idx_op_items_product_id ON operation_items(product_id)")
db.execute("CREATE INDEX IF NOT EXISTS idx_lots_product_id ON lots(product_id)")
db.execute("CREATE INDEX IF NOT EXISTS idx_lots_product_remaining ON lots(product_id, remaining_qty)")
```

---

### [HIGH-9] Отсутствие security headers

**Расположение:** `app.py` (везде)

**Проблема:**
Приложение не устанавливает `Content-Security-Policy`, `X-Frame-Options`, `X-Content-Type-Options`. Возможны XSS и clickjacking.

**Исправление:**
```python
@app.after_request
def add_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    return response
```

---

### [HIGH-10] Недостаточная валидация загружаемых файлов

**Расположение:** `app.py:~384–397`

**Проблема:**
При загрузке логотипа нет проверки:
- Размера файла (DoS через огромный файл)
- MIME-типа (расширение ≠ содержимое; SVG может содержать XSS)

**Исправление:**
```python
MAX_LOGO_SIZE = 2 * 1024 * 1024  # 2 MB

logo_file.seek(0, 2)
if logo_file.tell() > MAX_LOGO_SIZE:
    flash('Файл слишком большой (макс. 2 МБ)')
    return redirect(url_for('settings'))
logo_file.seek(0)

# Проверить первые байты на соответствие заявленному расширению
```

---

## 🟡 СРЕДНИЕ

---

### [MED-1] `inventory_complete` не проверяет права доступа

**Расположение:** `app.py:~1080`

**Проблема:**
Роут не проверяет, что текущий пользователь является владельцем инвентаризации или администратором.

**Исправление:**
```python
inv = db.execute("SELECT * FROM inventory_sessions WHERE id=?", (inv_id,)).fetchone()
if inv['user_id'] != current_user.id and current_user.role != 'admin':
    abort(403)
```

---

### [MED-2] Race condition в `inventory_complete`

**Расположение:** `app.py:~1080–1120`

**Проблема:**
Между проверкой статуса сессии и её обновлением нет блокировки. При параллельных запросах корректировки могут примениться дважды.

**Исправление:** Использовать `BEGIN IMMEDIATE` транзакцию и проверять статус внутри неё.

---

### [MED-3] `debug=True` захардкожен в `__main__`

**Расположение:** `app.py:~1389`

**Проблема:**
При случайном запуске `python app.py` в продакшене включится Werkzeug debugger с возможностью выполнения кода.

**Исправление:**
```python
if __name__ == "__main__":
    init_db()
    debug = os.environ.get('FLASK_DEBUG', '0') == '1'
    app.run(debug=debug, host='0.0.0.0', port=5000)
```

---

### [MED-4] Внутренний `from datetime import datetime` в `stock_print`

**Расположение:** `app.py:~1150`

**Проблема:**
`from datetime import datetime` внутри функции перекрывает уже импортированный модуль с `timezone`. Вместо UTC используется локальное серверное время.

**Исправление:** Удалить внутренний импорт, использовать `datetime.now(timezone.utc)`.

---

### [MED-5] `history` принимает `page` без валидации минимума

**Расположение:** `app.py:~900`

**Проблема:**
`page = int(request.args.get("page", 1))` без `try/except` и без `page >= 1`. При `?page=0` offset становится отрицательным.

**Исправление:**
```python
try:
    page = max(1, int(request.args.get("page", 1)))
except (ValueError, TypeError):
    page = 1
```

---

### [MED-6] `reorder_products` не валидирует id продуктов

**Расположение:** `app.py:~590`

**Проблема:**
JSON-массив `order` принимается без проверки. Можно передать несуществующие id, зондируя структуру БД.

**Исправление:**
```python
valid_ids = {row['id'] for row in db.execute("SELECT id FROM products").fetchall()}
if not all(pid in valid_ids for pid in order):
    abort(400)
```

---

### [MED-7] `init_db` не закрывает соединение при исключении

**Расположение:** `app.py:~120`

**Проблема:**
`db.close()` только в конце функции. При исключении в процессе миграции соединение утекает.

**Исправление:**
```python
def init_db():
    db = sqlite3.connect(DB_PATH)
    try:
        # ... вся логика ...
    finally:
        db.close()
```

---

### [MED-8] Публичный доступ к `uploaded_file` без ограничений

**Расположение:** `app.py:~256`

**Проблема:**
Роут `/uploads/<path:filename>` доступен без аутентификации. Если в `UPLOAD_FOLDER` попадут другие файлы — они будут публично доступны.

**Исправление:**
```python
@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    safe_name = os.path.basename(filename)
    if safe_name != filename:
        abort(403)
    return send_from_directory(UPLOAD_FOLDER, safe_name)
```

---

### [MED-9] Отсутствие обработки ошибок fetch-запросов в JS

**Расположение:** `templates/products.html:~208–218`

**Проблема:**
Сетевые ошибки при `fetch('/products/reorder', ...)` молча игнорируются.

**Исправление:**
```javascript
fetch('/products/reorder', { ... })
  .then(r => { if (!r.ok) throw new Error(r.status); })
  .catch(err => {
    console.error('Reorder failed:', err);
    // Показать пользователю сообщение об ошибке
  });
```

---

### [MED-10] Нет обработчиков 404 и 500 ошибок

**Расположение:** `app.py`

**Проблема:**
При ошибках пользователь видит технические страницы Flask, раскрывающие детали реализации.

**Исправление:**
```python
@app.errorhandler(404)
def not_found(e):
    return render_template('errors/404.html'), 404

@app.errorhandler(500)
def server_error(e):
    get_db().rollback()
    return render_template('errors/500.html'), 500
```

---

### [MED-11] Отсутствие валидации входных данных формы операции (отрицательные значения)

**Расположение:** `app.py:~714–730`

**Проблема:**
Количество и цена принимаются без проверки на отрицательные значения и чрезмерно большие числа.

**Исправление:**
```python
if raw_qty <= 0 or raw_qty > 1_000_000:
    flash('Некорректное количество')
    return redirect(...)
if price is not None and (price < 0 or price > 10_000_000):
    flash('Некорректная цена')
    return redirect(...)
```

---

## 🔵 НИЗКИЕ

---

### [LOW-1] Лишний импорт `datetime` в `calc_danger_thresholds`

**Расположение:** `app.py:~84`

`from datetime import datetime, timedelta` внутри функции — модули уже доступны на уровне файла. Удалить внутренний импорт.

---

### [LOW-2] Отсутствует индекс `lots(product_id, remaining_qty)`

FIFO-запрос в `fifo_consume` делает `WHERE product_id=? AND remaining_qty > 0` — нет составного индекса. При большом количестве лотов — полное сканирование.

```python
db.execute("CREATE INDEX IF NOT EXISTS idx_lots_product_remaining ON lots(product_id, remaining_qty)")
```

---

### [LOW-3] Отсутствует конфигурация логирования

**Расположение:** `app.py:~17`

`logger = logging.getLogger(__name__)` без настройки handler'ов. Сообщения могут не выводиться в продакшене.

```python
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s: %(message)s'
)
```

---

### [LOW-4] Нет единого места для констант

Константы разбросаны по файлу (`ALLOWED_LOGO_EXT`, `COMMON_TIMEZONES`, `ADJUST_REASONS`). Рекомендуется вынести в отдельный блок в начале файла или в `constants.py`.

---

### [LOW-5] Health check endpoint

Добавить простой endpoint для мониторинга доступности:
```python
@app.route('/health')
def health():
    return {'status': 'ok', 'ts': datetime.now(timezone.utc).isoformat()}
```

---

### [LOW-6] Несогласованное форматирование дат в шаблонах

**Расположение:** `templates/*.html`

В нескольких местах используется `{{ value[:10].replace('-','.') }}` вместо существующего фильтра `localdt`. Унифицировать через фильтр или добавить отдельный `date_short`.

---

## ✅ TODO LIST

### 🔴 Критические — исправить немедленно
- [ ] Обернуть `create_operation` и `update_operation` в явную транзакцию с rollback — `app.py:~630–680`, `~700–770`
- [ ] Устранить дублирование завершения инвентаризации (`inventory_session` ↔ `inventory_complete`) — `app.py:~1020–1120`
- [ ] Исправить `fifo_remove_lot`: при `consumed > 0` удалять связанные `lot_consumptions` — `app.py:~530–540`
- [ ] Добавить валидацию `op_type` перед INSERT — `app.py:~611`
- [ ] Добавить проверку остатка перед OUT-операцией — `app.py:~660`, `~760`

### 🟠 Высокие — исправить до релиза
- [ ] Переписать `inject_globals` для использования `get_db()` — `app.py:~240–248`
- [ ] Кэшировать `timezone` в `g` для `local_dt_filter` — `app.py:~220–232`
- [ ] Рефакторить SQL в `stats_detail`: убрать текстовую замену — `app.py:~840–865`
- [ ] Унифицировать требования к паролю (`change_password` = `setup`) — `app.py:~420`
- [ ] Добавить rate limiting на `/login` и `/setup`
- [ ] Валидировать `timezone` перед сохранением — `app.py:~320`
- [ ] Устранить N+1 запрос в истории операций — `app.py:~1009–1018`
- [ ] Добавить индексы `operation_items`, `lots` в `init_db`
- [ ] Добавить security headers через `@app.after_request`
- [ ] Добавить проверку размера и MIME-типа при загрузке файла — `app.py:~384–397`

### 🟡 Средние — исправить в текущем спринте
- [ ] Добавить проверку прав в `inventory_complete` — `app.py:~1080`
- [ ] Добавить `BEGIN IMMEDIATE` в `inventory_complete` против race condition
- [ ] Заменить `debug=True` на `FLASK_DEBUG` env — `app.py:~1389`
- [ ] Удалить внутренний `from datetime import datetime` в `stock_print` — `app.py:~1150`
- [ ] Добавить `max(1, ...)` и `try/except` для `page` в `history` — `app.py:~900`
- [ ] Валидировать id продуктов в `reorder_products` — `app.py:~590`
- [ ] Обернуть тело `init_db` в `try/finally` — `app.py:~120`
- [ ] Ограничить публичный доступ к `/uploads/<path>` — `app.py:~256`
- [ ] Добавить `.catch()` к fetch-запросам в шаблонах
- [ ] Добавить обработчики `@app.errorhandler(404)` и `@app.errorhandler(500)`
- [ ] Валидировать отрицательные значения qty/price в форме операции

### 🔵 Низкие — по возможности
- [ ] Удалить лишний `from datetime import datetime` в `calc_danger_thresholds` — `app.py:~84`
- [ ] Добавить индекс `idx_lots_product_remaining` — `init_db`
- [ ] Настроить logging (`basicConfig` или `RotatingFileHandler`)
- [ ] Вынести константы в единый блок
- [ ] Добавить `/health` endpoint
- [ ] Унифицировать форматирование дат в шаблонах

---

## 📝 Что не является проблемой (ложные тревоги из других отчётов)

- **«SQL Injection» в `stats_detail`** — все пользовательские значения передаются через `?`-параметры; динамически строится только структура запроса (какие условия включать), не данные. Это архитектурная хрупкость (HIGH-3), но не классическая инъекция.
- **«CSRF на GET-запросах»** — маршруты, изменяющие состояние, уже используют `methods=['POST']`; использование `CSRFProtect` покрывает их.
- **«Неиспользуемые импорты»** — все импорты в файле задействованы.
- **sitemap.xml / robots.txt** — нерелевантно для внутренней WMS-системы без публичного доступа.

---

*Сформировано: 2026-03-13 | Проанализировано строк кода: ~5000+*
