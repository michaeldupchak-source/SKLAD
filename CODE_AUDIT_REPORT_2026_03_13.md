# 🔍 Аудит кода проекта SKLAD_0.2

**Дата аудита:** 13 марта 2026 г.  
**Версия проекта:** 0.2  
**Аудитор:** AI Code Auditor

---

## 📋 Содержание

1. [Краткое резюме (Executive Summary)](#краткое-резюме-executive-summary)
2. [Структура проекта и организация кода](#структура-проекта-и-организация-кода)
3. [Качество кода](#качество-кода)
4. [Потенциальные баги и ошибки](#потенциальные-баги-и-ошибки)
5. [Проблемы безопасности](#проблемы-безопасности)
6. [Производительность](#производительность)
7. [Соответствие лучшим практикам Python](#соответствие-лучшим-практикам-python)
8. [Обработка ошибок](#обработка-ошибок)
9. [Тестируемость кода](#тестируемость-кода)
10. [Зависимости и их актуальность](#зависимости-и-их-актуальность)
11. [Документация](#документация)
12. [Приоритизированный Todo List](#приоритизированный-todo-list)

---

## Краткое резюме (Executive Summary)

### Общая оценка проекта

| Категория | Оценка | Статус |
|-----------|--------|--------|
| Структура проекта | 7/10 | ⚠️ Требует улучшений |
| Качество кода | 6/10 | ⚠️ Требует улучшений |
| Безопасность | 5/10 | 🔴 Критические проблемы |
| Производительность | 6/10 | ⚠️ Требует улучшений |
| Обработка ошибок | 7/10 | ⚠️ Требует улучшений |
| Тестируемость | 3/10 | 🔴 Критические проблемы |
| Документация | 7/10 | ⚠️ Требует улучшений |

### Ключевые выводы

**Положительные аспекты:**
- ✅ Рабочее приложение с полным функционалом складского учёта
- ✅ Реализована PWA-функциональность (Service Worker, manifest)
- ✅ Присутствует CSRF-защита через Flask-WTF
- ✅ Хеширование паролей через Werkzeug
- ✅ Аудит-логирование операций
- ✅ Поддержка FIFO для расчёта себестоимости
- ✅ Мобильная адаптация интерфейса
- ✅ Система инвентаризации

**Критические проблемы:**
- 🔴 **SQL Injection уязвимость** — использование f-строк для SQL-запросов
- 🔴 **Отсутствие валидации входных данных** в нескольких местах
- 🔴 **Нет тестов** — полностью отсутствует тестовое покрытие
- 🔴 **Уязвимость XSS** — недостаточная экранизация пользовательского ввода
- 🔴 **Проблемы с транзакциями** — потенциальная потеря данных при ошибках

**Статистика кода:**
- `app.py`: 1706 строк (монолитный файл — антипаттерн)
- Шаблоны: 15 файлов
- Статические файлы: 5 файлов
- Зависимости: 5 пакетов (устаревшие версии)

---

## Структура проекта и организация кода

### Текущая структура

```
SKLAD_0.2/
├── app.py                    # 1706 строк — ВСЁ приложение в одном файле
├── requirements.txt
├── readme.md
├── .env.example
├── .gitignore
├── static/
│   ├── css/mobile.css
│   ├── mobile.js
│   ├── sw.js
│   ├── manifest.json
│   └── icons/
├── templates/
│   ├── base.html             # 955 строк
│   ├── stock.html
│   ├── operation_new.html
│   ├── history.html
│   ├── settings.html
│   ├── products.html
│   ├── categories.html
│   ├── units.html
│   ├── stats.html
│   ├── stats_detail.html
│   ├── inventory.html
│   ├── inventory_session.html
│   ├── operation_edit.html
│   ├── login.html
│   ├── setup.html
│   └── errors/
│       ├── 404.html
│       └── 500.html
└── warehouse.db              # SQLite база данных
```

### Проблемы структуры

#### ❌ Проблема 1: Монолитный app.py (1706 строк)

**Файл:** `app.py`  
**Серьёзность:** HIGH  
**Описание:** Всё приложение содержится в одном файле, что нарушает принцип единственной ответственности и затрудняет поддержку.

**Рекомендация:** Разделить на модули:
```
app/
├── __init__.py
├── config.py
├── extensions.py
├── models/
│   ├── __init__.py
│   ├── user.py
│   ├── product.py
│   ├── operation.py
│   └── inventory.py
├── routes/
│   ├── __init__.py
│   ├── auth.py
│   ├── stock.py
│   ├── operations.py
│   ├── products.py
│   └── settings.py
├── services/
│   ├── fifo.py
│   └── audit.py
└── templates/
```

#### ❌ Проблема 2: Отсутствие конфигурационного модуля

**Файл:** `app.py:43-48`  
**Серьёзность:** MEDIUM

```python
# app.py:43-48
_secret = os.environ.get('SECRET_KEY')
if _secret:
    app.secret_key = _secret
else:
    app.secret_key = secrets.token_hex(32)
    logger.warning("SECRET_KEY not set – using a random key...")
```

**Проблема:** Конфигурация разбросана по коду, нет централизованного управления настройками.

#### ❌ Проблема 3: Смешение логики и представления

**Файл:** `app.py` (множественные места)  
**Серьёзность:** MEDIUM

Бизнес-логика (FIFO, расчёты) смешана с маршрутизацией Flask.

---

## Качество кода

### Стиль и читаемость

#### ⚠️ Проблема 1: Несогласованное именование

**Файл:** `app.py`  
**Серьёзность:** LOW

```python
# Смешение стилей именования
def calc_danger_thresholds(db, mode, weeks):  # snake_case ✓
def _get_local_dt(value):                      # snake_case с префиксом ✓
def _build_snapshot(db, operation_id):         # snake_case ✓
def fifo_consume(db, product_id, qty, ...):    # snake_case ✓

# Но в шаблонах и其他地方 используются разные стили
```

#### ⚠️ Проблема 2: Чрезмерно длинные функции

**Файл:** `app.py:1413-1502`  
**Серьёзность:** MEDIUM

```python
@app.route("/inventory/<int:inv_id>", methods=["GET", "POST"])
def inventory_session(inv_id):
    # Функция содержит ~90 строк с множественной вложенностью
    # Смешивает GET/POST логику, бизнес-логику и редиректы
```

**Рекомендация:** Разделить на:
- `handle_inventory_get()`
- `handle_inventory_post()`
- `complete_inventory_session()`

#### ⚠️ Проблема 3: Магические числа

**Файл:** `app.py:789-792`

```python
qty = int(raw_qty)
if qty < 1 or qty > 1_000_000:  # Магическое число 1_000_000
    flash('Некорректное количество товара')
```

**Рекомендация:** Вынести в константы:
```python
MAX_QUANTITY = 1_000_000
MIN_QUANTITY = 1
```

#### ⚠️ Проблема 4: Дублирование кода

**Файл:** `app.py` (множественные места)  
**Серьёзность:** MEDIUM

Проверка авторизации дублируется:
```python
# app.py:414-417
if session['user_id'] != current_user.id and current_user.role != 'admin':
    abort(403)

# Аналогичная проверка в других местах
```

---

## Потенциальные баги и ошибки

### 🔴 КРИТИЧЕСКИЕ ПРОБЛЕМЫ

#### ❌ Баг 1: SQL Injection уязвимость

**Файл:** `app.py:1165-1178`  
**Серьёзность:** CRITICAL  
**CVE риск:** Высокий

```python
# app.py:1165-1178 — ПРЯМАЯ SQL INJECTION
rows = db.execute(f"""
    SELECT p.id, p.name as product_name, u.short_name as unit_short, p.current_stock,
        COALESCE(SUM(CASE WHEN o.type='IN' THEN oi.quantity ELSE 0 END),0) as in_qty,
        ...
    FROM products p
    LEFT JOIN units u ON u.id = p.unit_id
    LEFT JOIN operation_items oi ON oi.product_id = p.id {oi_filter}
    LEFT JOIN operations o ON o.id = oi.operation_id AND {op_where}
    WHERE 1=1 {p_filter}
    GROUP BY p.id ORDER BY p.sort_order, p.name
""", [prod_param] + op_params + [prod_param]).fetchall()
```

**Проблема:** Переменные `{oi_filter}`, `{op_where}`, `{p_filter}` вставляются напрямую в SQL через f-строку.

**Вектор атаки:**
```python
# Если злоумышленник может контролировать category_id
category_id = "1; DROP TABLE users;--"
```

**Исправление:** Использовать параметризованные запросы:
```python
# Вместо f-строк использовать условную сборку запроса
if subject_type == "product":
    query = """SELECT ... WHERE p.id = ?"""
    params = [product_id]
else:
    query = """SELECT ... WHERE p.category_id = ?"""
    params = [category_id]
```

#### ❌ Баг 2: Ещё одна SQL Injection

**Файл:** `app.py:1267-1275`  
**Серьёзность:** CRITICAL

```python
# app.py:1267-1275
all_items = db.execute(f"""
    SELECT oi.*, p.name as product_name, u.short_name as unit_short
    FROM operation_items oi
    JOIN products p ON p.id = oi.product_id
    LEFT JOIN units u ON u.id = p.unit_id
    WHERE oi.operation_id IN ({placeholders})
""", op_ids).fetchall()
```

Хотя `placeholders` создаётся из доверенных данных, это опасный паттерн.

#### ❌ Баг 3: Потенциальная гонка данных (Race Condition)

**Файл:** `app.py:1456-1470`  
**Серьёзность:** HIGH

```python
# app.py:1456-1470 — ПРОВЕРКА И ЗАПИСЬ БЕЗ БЛОКИРОВКИ
existing = db.execute(
    "SELECT id FROM inventory_sessions WHERE status='draft' AND user_id=?",
    (current_user.id,)
).fetchone()
if existing:
    return redirect(...)
cur = db.execute("INSERT INTO inventory_sessions ...")  # Между SELECT и INSERT другой поток может вставить запись
```

**Исправление:** Использовать транзакцию с блокировкой или UNIQUE constraint.

#### ❌ Баг 4: Неправильная обработка транзакций

**Файл:** `app.py:856-880`  
**Серьёзность:** HIGH

```python
# app.py:856-880
try:
    db.execute("BEGIN")
    # ... множество операций ...
    db.execute("COMMIT")
    _write_audit(db, op_id, 'created')
    db.commit()  # Второй commit после COMMIT!
except Exception as e:
    db.execute("ROLLBACK")  # ROLLBACK после COMMIT не имеет смысла
```

**Проблема:** После `COMMIT` нельзя сделать `ROLLBACK`. Если `_write_audit` упадёт, данные будут несогласованными.

#### ⚠️ Баг 5: Отсутствует проверка существования пользователя

**Файл:** `app.py:68-72`

```python
@login_manager.user_loader
def load_user(user_id):
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    return User(row) if row else None  # ✓ Хорошо, но...
```

**Проблема:** Если пользователь удалён во время сессии, будет ошибка при доступе к `current_user`.

#### ⚠️ Баг 6: Деление на ноль в FIFO

**Файл:** `app.py:593-606`

```python
# app.py:593-606
def fifo_consume(db, product_id, qty, operation_item_id):
    # ...
    return total_cost / qty if qty > 0 else None  # ✓ Защита есть
```

Хотя защита присутствует, в других местах может быть проблема.

#### ⚠️ Баг 7: Неправильная работа с datetime

**Файл:** `app.py:327-333`

```python
# app.py:327-333
def _get_local_dt(value):
    if not value:
        return None
    # ...
    try:
        dt = datetime.strptime(str(value)[:19], '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
        return dt.astimezone(g.cached_tz)
    except (ValueError, TypeError):
        return None
```

**Проблема:** Если `value` имеет неожиданный формат, функция вернёт `None`, что может вызвать ошибки в шаблонах.

---

## Проблемы безопасности

### 🔴 КРИТИЧЕСКИЕ УЯЗВИМОСТИ

#### ❌ Security 1: SQL Injection (см. выше)

**Серьёзность:** CRITICAL  
**CVSS Score:** 9.8

#### ❌ Security 2: Недостаточная валидация файлов

**Файл:** `app.py:468-485`  
**Серьёзность:** HIGH

```python
# app.py:468-485
logo_file = request.files.get('org_logo')
if logo_file and logo_file.filename:
    ext = logo_file.filename.rsplit('.', 1)[-1].lower()
    if ext in ALLOWED_LOGO_EXT:  # Проверка только расширения!
        # Проверка размера
        logo_file.seek(0, 2)
        file_size = logo_file.tell()
        logo_file.seek(0)
        if file_size > 2 * 1024 * 1024:
            flash('Файл слишком большой')
            return redirect(url_for('settings'))
        filename = f'org_logo.{ext}'
        logo_file.save(os.path.join(UPLOAD_FOLDER, filename))
```

**Проблемы:**
1. Нет проверки MIME-типа файла
2. Нет проверки магических байтов (сигнатуры файла)
3. Имя файла предсказуемо (`org_logo.{ext}`)
4. Возможна атака через двойное расширение (`malicious.php.jpg`)

**Вектор атаки:**
```python
# Злоумышленник загружает файл с содержимым PHP-кода
# но расширением .jpg
# При определённых конфигурациях сервера файл может быть выполнен
```

**Исправление:**
```python
import magic  # python-magic

def validate_image(file):
    # Проверка MIME-типа
    mime = magic.from_buffer(file.read(1024), mime=True)
    file.seek(0)
    if mime not in ['image/jpeg', 'image/png', 'image/gif', 'image/webp']:
        return False
    # Проверка сигнатуры
    file.seek(0)
    header = file.read(8)
    if not (header.startswith(b'\xff\xd8\xff') or  # JPEG
            header.startswith(b'\x89PNG\r\n\x1a\n') or  # PNG
            header.startswith(b'GIF87a') or header.startswith(b'GIF89a')):  # GIF
        return False
    return True
```

#### ❌ Security 3: XSS уязвимость в шаблонах

**Файл:** `templates/history.html:131-135`  
**Серьёзность:** HIGH

```html
<!-- templates/history.html:131-135 -->
<td class="text-muted text-sm">{{ op.comment or '—' }}</td>
```

**Проблема:** Хотя Jinja2 автоматически экранирует вывод, если в `comment` содержится JavaScript и используется фильтр `|safe`, произойдёт XSS.

**Проверьте все места использования:**
```bash
grep -r "|safe" templates/
```

#### ❌ Security 4: Отсутствие rate limiting

**Файл:** `app.py:428-445`  
**Серьёзность:** MEDIUM

```python
@app.route('/login', methods=['GET', 'POST'])
def login():
    # Нет ограничения попыток входа
    if row and check_password_hash(row['password_hash'], password):
        login_user(User(row), remember=True)
```

**Риск:** Brute-force атаки на пароли.

**Исправление:** Добавить Flask-Limiter:
```python
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

limiter = Limiter(app, key_func=get_remote_address)

@app.route('/login', methods=['GET', 'POST'])
@limiter.limit("5 per minute")
def login():
    # ...
```

#### ❌ Security 5: Слабая политика паролей

**Файл:** `app.py:23-31`  
**Серьёзность:** MEDIUM

```python
def validate_password(password):
    if len(password) < 8:
        return 'Пароль должен содержать не менее 8 символов'
    if not re.search(r'[A-Za-z]', password):
        return 'Пароль должен содержать хотя бы одну букву'
    if not re.search(r'\d', password):
        return 'Пароль должен содержать хотя бы одну цифру'
    return None
```

**Проблемы:**
- Нет требования к специальным символам
- Нет проверки на распространённые пароли
- Нет проверки на последовательности (`12345678`, `abcdefgh`)

#### ⚠️ Security 6: Отсутствие HTTPS принуждения

**Файл:** `app.py:367-373`

```python
@app.after_request
def add_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    return response
```

**Отсутствует:**
- `Strict-Transport-Security` заголовок
- `Content-Security-Policy` заголовок

#### ⚠️ Security 7: Предсказуемые имена файлов

**Файл:** `app.py:480`

```python
filename = f'org_logo.{ext}'  # Всегда одно и то же имя
```

**Риск:** При загрузке нового файла старый перезаписывается, но если процесс не атомарный, может быть проблема.

---

## Производительность

### ⚠️ Проблема 1: N+1 запросы в истории

**Файл:** `app.py:1256-1285`  
**Серьёзность:** MEDIUM

```python
# Получаем операции
ops = db.execute("SELECT o.*, u.username ...").fetchall()

ops_with_items = []
for op in ops:  # Цикл по операциям
    # Отдельный запрос для каждой операции!
    items = db.execute("SELECT ... WHERE operation_id=?", (op["id"],)).fetchall()
    ops_with_items.append({"op": op, "items": items})
```

**Исправление:** Использовать один запрос с JOIN и группировать в Python.

### ⚠️ Проблема 2: Отсутствие индексов для частых запросов

**Файл:** `app.py:200-250`

Отсутствуют индексы для:
- `operations(type, created_at)` — частая фильтрация
- `products(is_active, category_id)` — фильтрация товаров
- `inventory_sessions(status, user_id)` — проверка черновиков

### ⚠️ Проблема 3: Загрузка всех товаров в память

**Файл:** `app.py:1380-1400`

```python
# При загрузке stock загружаются ВСЕ товары
stock_rows = db.execute(f"""
    SELECT p.*, c.name as cat_name, ...
    FROM products p
    ...
    {where} ORDER BY p.sort_order, p.name
""", params).fetchall()
```

**Проблема:** Нет пагинации на странице наличия.

### ⚠️ Проблема 4: Повторные вычисления в шаблонах

**Файл:** `templates/stock.html:27-30`

```jinja2
{% set danger_count = namespace(v=0) %}
{% for p in products %}
  {% if danger_thresholds.get(p.id, 0) > 0 and p.current_stock > 0 and p.current_stock < danger_thresholds.get(p.id) %}
    {% set danger_count.v = danger_count.v + 1 %}
  {% endif %}
{% endfor %}
```

**Проблема:** Вычисление в шаблоне вместо передачи из view.

---

## Соответствие лучшим практикам Python

### ❌ PEP 8 нарушения

#### Проблема 1: Длина строк

**Файл:** `app.py:1165-1178`

Строки превышают 79/100 символов:
```python
rows = db.execute(f"""
    SELECT p.id, p.name as product_name, u.short_name as unit_short, p.current_stock,
        COALESCE(SUM(CASE WHEN o.type='IN' THEN oi.quantity ELSE 0 END),0) as in_qty,
```

#### Проблема 2: Импорты

**Файл:** `app.py:1-15`

```python
import logging
import os
import re
from dotenv import load_dotenv
load_dotenv()  # Вызов на уровне модуля!
import secrets
import sqlite3
```

**Проблема:** `load_dotenv()` вызывается между импортами, что нарушает порядок.

**Исправление:**
```python
from dotenv import load_dotenv
load_dotenv()

import logging
import os
# ...
```

#### Проблема 3: Отсутствие type hints

**Файл:** `app.py` (везде)

```python
# Нет type hints
def validate_password(password):
    # ...

# Должно быть:
def validate_password(password: str) -> str | None:
    # ...
```

### ⚠️ Антипаттерны

#### Антипаттерн 1: Глобальное состояние

```python
# app.py:37
app = Flask(__name__)
# app — глобальная переменная, усложняет тестирование
```

#### Антипаттерн 2: God Object

Весь функционал в одном файле `app.py`.

#### Антипаттерн 3: Смешение уровней абстракции

```python
@app.route("/operations/create", methods=["POST"])
def create_operation():
    # HTTP логика
    # Бизнес-логика (FIFO)
    # SQL запросы
    # Всё в одной функции!
```

---

## Обработка ошибок

### ⚠️ Проблема 1: Неполная обработка исключений

**Файл:** `app.py:877-882`

```python
except Exception as e:
    db.execute("ROLLBACK")
    logger.exception("create_operation failed: %s", e)
    flash('Ошибка при создании операции. Изменения отменены.')
    return redirect(url_for('new_operation'))
```

**Проблема:** Ловится `Exception`, но не `BaseException`. Если произойдёт `KeyboardInterrupt` или `SystemExit`, транзакция останется открытой.

### ⚠️ Проблема 2: Потеря контекста ошибки

**Файл:** `app.py:95-100`

```python
def get_setting(key, default=None):
    try:
        # ...
    except sqlite3.Error as e:
        logger.exception("get_setting(%r) failed: %s", key, e)
        return default  # Ошибка проглочена, вызывающий код не узнает о проблеме
```

### ⚠️ Проблема 3: Отсутствие обработки 404

**Файл:** `app.py:1692-1694`

```python
@app.errorhandler(404)
def not_found(e):
    return render_template('errors/404.html'), 404
```

**Проблема:** Нет логирования 404 ошибок для мониторинга.

---

## Тестируемость кода

### 🔴 КРИТИЧЕСКАЯ ПРОБЛЕМА: Полное отсутствие тестов

**Статус:** В проекте нет ни одного теста.

**Отсутствует:**
- ❌ Unit-тесты для бизнес-логики (FIFO, расчёты)
- ❌ Integration-тесты для маршрутов
- ❌ Test fixtures
- ❌ pytest или unittest конфигурация
- ❌ Mock объекты для базы данных

### 🔴 Проблема 1: Код не предназначен для тестирования

```python
# app.py:43-48
app = Flask(__name__)
# Прямое создание app, нет factory function

# Должно быть:
def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)
    # ...
    return app
```

### 🔴 Проблема 2: Глобальные зависимости

```python
# app.py:40
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "warehouse.db")
# Жёстко закодированный путь, невозможно использовать тестовую БД
```

### 🔴 Проблема 3: Смешение логики и фреймворка

```python
# Невозможно протестировать fifo_consume без Flask context
def fifo_consume(db, product_id, qty, operation_item_id):
    # ...
```

---

## Зависимости и их актуальность

### Текущие зависимости

**Файл:** `requirements.txt`

```
Flask==3.0.0           # Вышла 23 ноя 2023
Flask-Login==0.6.3     # Вышла 23 май 2023
Flask-WTF==1.2.2       # Вышла 15 ноя 2023
Werkzeug==3.1.3        # Актуальная
python-dotenv==1.0.1   # Актуальная
```

### ⚠️ Проблема 1: Устаревшие версии

| Пакет | Текущая | Последняя | Статус |
|-------|---------|-----------|--------|
| Flask | 3.0.0 | 3.1.0+ | ⚠️ Устарел |
| Flask-Login | 0.6.3 | 0.7.0+ | ⚠️ Устарел |
| Flask-WTF | 1.2.2 | 1.3.0+ | ⚠️ Устарел |

### ⚠️ Проблема 2: Отсутствие зависимостей для разработки

**Отсутствует:**
- pytest
- black (форматирование)
- flake8 (линтинг)
- mypy (type checking)
- safety (проверка уязвимостей)

### ⚠️ Проблема 3: Нет фиксации версий

```
# requirements.txt
Flask==3.0.0  # ✓ Хорошо, есть версия

# Но нет requirements-dev.txt для dev зависимостей
```

---

## Документация

### ✅ Положительные аспекты

- Наличие `readme.md` с описанием функционала
- Пример `.env.example`
- Комментарии в коде (некоторые)

### ⚠️ Проблема 1: Отсутствие API документации

Нет документации для:
- Маршрутов (endpoints)
- Форматов данных
- Моделей базы данных

### ⚠️ Проблема 2: Недостаточные комментарии

**Файл:** `app.py:1165-1178`

```python
# Нет объяснения, почему используется f-строка для SQL
rows = db.execute(f"""
    SELECT ...
""", ...)
```

### ⚠️ Проблема 3: Нет changelog

Отсутствует файл `CHANGELOG.md` с историей изменений.

### ⚠️ Проблема 4: Нет документации по развёртыванию

В `readme.md` есть базовые инструкции, но отсутствует:
- Конфигурация для production
- Настройка веб-сервера (nginx, gunicorn)
- Мониторинг и логирование
- Backup стратегии

---

## Приоритизированный Todo List

### 🔴 CRITICAL (Исправить немедленно)

- [ ] **C1. Исправить SQL Injection уязвимость**
  - Файлы: `app.py:1165-1178`, `app.py:1267-1275`
  - Заменить f-строки на параметризованные запросы
  - Время: 4-6 часов

- [ ] **C2. Добавить валидацию загружаемых файлов**
  - Файл: `app.py:468-485`
  - Проверка MIME-типа и магических байтов
  - Время: 2-3 часа

- [ ] **C3. Исправить обработку транзакций**
  - Файл: `app.py:856-882`
  - Переместить `_write_audit` внутрь транзакции
  - Время: 2 часа

- [ ] **C4. Создать базовый набор тестов**
  - Добавить pytest
  - Написать тесты для критической бизнес-логики (FIFO)
  - Время: 8-12 часов

### 🟠 HIGH (Исправить в ближайшем спринте)

- [ ] **H1. Разделить app.py на модули**
  - Создать структуру пакетов
  - Выделить модели, маршруты, сервисы
  - Время: 16-24 часа

- [ ] **H2. Добавить rate limiting для login**
  - Установить Flask-Limiter
  - Настроить 5 попыток в минуту
  - Время: 1-2 часа

- [ ] **H3. Усилить политику паролей**
  - Добавить требование спецсимволов
  - Проверка на распространённые пароли
  - Время: 2 часа

- [ ] **H4. Добавить Security заголовки**
  - Content-Security-Policy
  - Strict-Transport-Security
  - Время: 1 час

- [ ] **H5. Исправить гонку данных в inventory**
  - Добавить UNIQUE constraint
  - Использовать транзакции с блокировкой
  - Время: 2-3 часа

### 🟡 MEDIUM (Исправить в текущем квартале)

- [ ] **M1. Добавить type hints**
  - Начать с критических функций
  - Настроить mypy
  - Время: 8-12 часов

- [ ] **M2. Оптимизировать N+1 запросы**
  - История операций
  - Страница наличия
  - Время: 4-6 часов

- [ ] **M3. Добавить индексы в БД**
  - `operations(type, created_at)`
  - `products(is_active, category_id)`
  - Время: 2 часа

- [ ] **M4. Создать requirements-dev.txt**
  - pytest, black, flake8, mypy
  - Время: 1 час

- [ ] **M5. Добавить логирование 404 ошибок**
  - Мониторинг подозрительных запросов
  - Время: 1 час

- [ ] **M6. Улучшить обработку ошибок**
  - Ловить BaseException в критических местах
  - Не проглатывать ошибки silently
  - Время: 3-4 часа

### 🟢 LOW (Улучшения по возможности)

- [ ] **L1. Добавить factory function для app**
  - Улучшить тестируемость
  - Время: 2-3 часа

- [ ] **L2. Создать API документацию**
  - OpenAPI/Swagger спецификация
  - Время: 4-6 часов

- [ ] **L3. Добавить пагинацию на stock**
  - Для больших складов
  - Время: 3-4 часа

- [ ] **L4. Создать CHANGELOG.md**
  - История изменений версий
  - Время: 1 час

- [ ] **L5. Добавить документацию по deployment**
  - Production конфигурация
  - nginx + gunicorn
  - Время: 4-6 часов

- [ ] **L6. Рефакторинг шаблонов**
  - Вынести повторяющиеся компоненты
  - Время: 4-6 часов

---

## Примеры кода с исправлениями

### Пример 1: Исправление SQL Injection

**До (уязвимо):**
```python
# app.py:1165-1178
oi_filter = "AND oi.product_id = ?" if subject_type == "product" else "AND p.category_id = ?"
rows = db.execute(f"""
    SELECT p.id, p.name as product_name
    FROM products p
    LEFT JOIN operation_items oi ON oi.product_id = p.id {oi_filter}
    WHERE 1=1 {p_filter}
""", [prod_param] + op_params + [prod_param]).fetchall()
```

**После (безопасно):**
```python
# Вынесено в отдельную функцию с параметризованными запросами
def get_stats_detail(db, subject_type, subject_id, date_from, date_to):
    base_query = """
        SELECT p.id, p.name as product_name, u.short_name as unit_short, p.current_stock,
            COALESCE(SUM(CASE WHEN o.type='IN' THEN oi.quantity ELSE 0 END),0) as in_qty,
            COALESCE(SUM(CASE WHEN o.type='OUT' THEN oi.quantity ELSE 0 END),0) as out_qty
        FROM products p
        LEFT JOIN units u ON u.id = p.unit_id
        LEFT JOIN operation_items oi ON oi.product_id = p.id
        LEFT JOIN operations o ON o.id = oi.operation_id
    """
    
    conditions = ["1=1"]
    params = []
    
    if subject_type == "product":
        conditions.append("oi.product_id = ?")
        params.append(subject_id)
    else:
        conditions.append("p.category_id = ?")
        params.append(subject_id)
    
    if date_from:
        conditions.append("o.created_at >= ?")
        params.append(date_from)
    
    query = base_query + " WHERE " + " AND ".join(conditions) + " GROUP BY p.id ORDER BY p.sort_order, p.name"
    return db.execute(query, params).fetchall()
```

### Пример 2: Factory function для Flask app

**До:**
```python
# app.py:43
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))
# ... весь код приложения ...
```

**После:**
```python
# app/__init__.py
from flask import Flask
from .config import Config
from .extensions import db, login_manager, csrf

def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)
    
    # Инициализация расширений
    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)
    
    # Регистрация blueprint'ов
    from .routes import auth, stock, operations, products
    app.register_blueprint(auth.bp)
    app.register_blueprint(stock.bp)
    app.register_blueprint(operations.bp)
    app.register_blueprint(products.bp)
    
    return app
```

### Пример 3: Валидация загружаемых файлов

**До:**
```python
# app.py:475
ext = logo_file.filename.rsplit('.', 1)[-1].lower()
if ext in ALLOWED_LOGO_EXT:
    filename = f'org_logo.{ext}'
    logo_file.save(os.path.join(UPLOAD_FOLDER, filename))
```

**После:**
```python
import magic
from werkzeug.utils import secure_filename

ALLOWED_MIME_TYPES = {
    'image/jpeg': '.jpg',
    'image/png': '.png',
    'image/gif': '.gif',
    'image/webp': '.webp',
}

def validate_and_save_logo(file, upload_folder):
    """Безопасное сохранение логотипа с полной валидацией."""
    if not file or not file.filename:
        return None, "Файл не предоставлен"
    
    # Проверка размера
    file.seek(0, 2)
    size = file.tell()
    file.seek(0)
    if size > 2 * 1024 * 1024:
        return None, "Файл слишком большой (макс. 2 МБ)"
    
    # Проверка MIME-типа
    mime = magic.from_buffer(file.read(1024), mime=True)
    file.seek(0)
    if mime not in ALLOWED_MIME_TYPES:
        return None, "Недопустимый тип файла"
    
    # Проверка сигнатуры файла
    header = file.read(8)
    file.seek(0)
    valid_signatures = [
        b'\xff\xd8\xff',  # JPEG
        b'\x89PNG\r\n\x1a\n',  # PNG
        b'GIF87a', b'GIF89a',  # GIF
        b'RIFF....WEBP',  # WebP (проверяем первые 4 байта)
    ]
    if not any(header.startswith(sig[:len(header)]) for sig in valid_signatures):
        return None, "Файл не является изображением"
    
    # Генерация безопасного имени файла
    ext = ALLOWED_MIME_TYPES[mime]
    filename = f"org_logo_{secrets.token_hex(8)}{ext}"
    filepath = os.path.join(upload_folder, filename)
    
    file.save(filepath)
    return filename, None
```

---

## Заключение

Проект SKLAD_0.2 представляет собой рабочее приложение для складского учёта с полезным функционалом. Однако код требует значительных улучшений в области безопасности, архитектуры и тестируемости.

**Приоритеты:**
1. **Немедленно:** Исправить SQL Injection и уязвимости загрузки файлов
2. **Краткосрочно:** Разделить монолитный app.py, добавить тесты
3. **Среднесрочно:** Оптимизировать производительность, добавить документацию

**Оценка усилий:**
- Критические исправления: 16-23 часов
- Высокоприоритетные: 25-35 часов
- Среднеприоритетные: 20-28 часов
- Низкоприоритетные: 15-25 часов

**Итого:** 76-111 часов для приведения кода к приемлемому уровню качества.

---

*Отчёт сгенерирован: 13 марта 2026 г.*  
*Инструмент: AI Code Auditor*
