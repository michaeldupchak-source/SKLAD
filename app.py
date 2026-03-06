import logging
import os
import re
from dotenv import load_dotenv
load_dotenv()
import secrets
import sqlite3
import urllib.parse
from datetime import datetime, timedelta, timezone
from sqlite3 import IntegrityError
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from flask import Flask, render_template, request, redirect, url_for, g, flash
from flask_login import (LoginManager, UserMixin,
                         login_user, logout_user, current_user)
from flask_wtf.csrf import CSRFProtect
from werkzeug.security import generate_password_hash, check_password_hash

logger = logging.getLogger(__name__)

app = Flask(__name__)
# Read secret key from environment; fall back to a random key in development.
_secret = os.environ.get('SECRET_KEY')
if _secret:
    app.secret_key = _secret
else:
    app.secret_key = secrets.token_hex(32)
    logger.warning("SECRET_KEY not set – using a random key (sessions will not persist across restarts)")

csrf = CSRFProtect(app)
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "warehouse.db")

login_manager = LoginManager(app)
login_manager.login_view = 'login'

# ── User model ─────────────────────────────────────────────
class User(UserMixin):
    def __init__(self, row):
        self.id       = row['id']
        self.username = row['username']
        self.role     = row['role']
        self.theme    = row['theme']

@login_manager.user_loader
def load_user(user_id):
    # Use a fresh connection here because load_user may be called outside a
    # request context (e.g. during app startup). Using sqlite3 directly keeps
    # the logic self-contained and avoids leaking a g.db to Flask-Login.
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    return User(row) if row else None

# ── DB helpers ─────────────────────────────────────────────
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db

@app.teardown_appcontext
def close_db(e=None):
    db = g.pop("db", None)
    if db:
        db.close()

def get_setting(key, default=None):
    try:
        with sqlite3.connect(DB_PATH) as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
        return row['value'] if row else default
    except sqlite3.Error as e:
        logger.exception("get_setting(%r) failed: %s", key, e)
        return default

def set_setting(db, key, value):
    db.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES (?,?)", (key, value))


def calc_danger_thresholds(db, mode, weeks):
    """
    Returns dict {product_id: avg_weekly_consumption} for all active products.
    mode: 'recent' — last N weeks only; 'alltime' — all history divided by N weeks.
    A product is considered dangerous when current_stock < threshold.
    """
    weeks = max(1, int(weeks))
    if mode == 'recent':
        from datetime import datetime, timedelta
        cutoff = (datetime.utcnow() - timedelta(weeks=weeks)).strftime('%Y-%m-%d %H:%M:%S')
        rows = db.execute("""
            SELECT oi.product_id, COALESCE(SUM(oi.quantity), 0) as total_out
            FROM operation_items oi
            JOIN operations o ON o.id = oi.operation_id
            WHERE o.type = 'OUT' AND o.created_at >= ?
            GROUP BY oi.product_id
        """, (cutoff,)).fetchall()
        return {r['product_id']: r['total_out'] / weeks for r in rows}
    else:  # alltime
        rows = db.execute("""
            SELECT oi.product_id, COALESCE(SUM(oi.quantity), 0) as total_out
            FROM operation_items oi
            JOIN operations o ON o.id = oi.operation_id
            WHERE o.type = 'OUT'
            GROUP BY oi.product_id
        """).fetchall()
        return {r['product_id']: r['total_out'] / weeks for r in rows}


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.execute("PRAGMA foreign_keys = ON")
    db.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        username      TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        role          TEXT NOT NULL DEFAULT 'user',
        theme         TEXT NOT NULL DEFAULT 'dark',
        created_at    TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS app_settings (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS categories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        description TEXT
    );
    CREATE TABLE IF NOT EXISTS units (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        short_name TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        category_id INTEGER REFERENCES categories(id),
        unit_id INTEGER REFERENCES units(id),
        description TEXT,
        current_stock REAL NOT NULL DEFAULT 0,
        is_active INTEGER NOT NULL DEFAULT 1,
        sort_order INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS operations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        type TEXT NOT NULL CHECK(type IN ('IN','OUT','ADJUST')),
        created_at TEXT NOT NULL,
        comment TEXT,
        user_id INTEGER REFERENCES users(id) ON DELETE SET NULL
    );
    -- Migration: add user_id to existing installs (safe if column already exists)
    CREATE INDEX IF NOT EXISTS idx_operations_user_id ON operations(user_id);
    CREATE TABLE IF NOT EXISTS inventory_sessions (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at  TEXT NOT NULL,
        completed_at TEXT,
        user_id     INTEGER REFERENCES users(id) ON DELETE SET NULL,
        status      TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','completed'))
    );
    CREATE TABLE IF NOT EXISTS inventory_items (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id   INTEGER NOT NULL REFERENCES inventory_sessions(id) ON DELETE CASCADE,
        product_id   INTEGER NOT NULL REFERENCES products(id),
        expected_qty REAL NOT NULL,
        actual_qty   REAL,
        delta        REAL,
        reason       TEXT,
        price        REAL
    );
    CREATE TABLE IF NOT EXISTS operation_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        operation_id INTEGER NOT NULL REFERENCES operations(id) ON DELETE CASCADE,
        product_id INTEGER NOT NULL REFERENCES products(id),
        quantity REAL NOT NULL,
        price_per_unit REAL,
        reason TEXT
    );
    CREATE TABLE IF NOT EXISTS lots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id INTEGER NOT NULL REFERENCES products(id),
        operation_item_id INTEGER REFERENCES operation_items(id) ON DELETE SET NULL,
        price_per_unit REAL NOT NULL,
        original_qty REAL NOT NULL,
        remaining_qty REAL NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS lot_consumptions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        operation_item_id INTEGER NOT NULL REFERENCES operation_items(id) ON DELETE CASCADE,
        lot_id INTEGER NOT NULL REFERENCES lots(id),
        quantity REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
    CREATE INDEX IF NOT EXISTS idx_operations_created_at ON operations(created_at);
    CREATE INDEX IF NOT EXISTS idx_products_name ON products(name);
    CREATE INDEX IF NOT EXISTS idx_products_category_id ON products(category_id);
    """)
    db.commit()
    # Runtime migration: add user_id to operations for existing databases
    existing = [r[1] for r in db.execute("PRAGMA table_info(operations)")]
    if 'user_id' not in existing:
        db.execute("ALTER TABLE operations ADD COLUMN user_id INTEGER REFERENCES users(id) ON DELETE SET NULL")
        db.commit()
    # Runtime migration: add is_active to products for existing databases
    existing_p = [r[1] for r in db.execute("PRAGMA table_info(products)")]
    if 'is_active' not in existing_p:
        db.execute("ALTER TABLE products ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1")
        db.commit()
    if 'sort_order' not in existing_p:
        db.execute("ALTER TABLE products ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0")
        # seed sort_order from current alphabetical order
        rows = db.execute("SELECT id FROM products ORDER BY name").fetchall()
        for i, (pid,) in enumerate(rows):
            db.execute("UPDATE products SET sort_order=? WHERE id=?", (i, pid))
        db.commit()
    # Runtime migration: add reason to operation_items
    existing_oi = [r[1] for r in db.execute("PRAGMA table_info(operation_items)")]
    if 'reason' not in existing_oi:
        db.execute("ALTER TABLE operation_items ADD COLUMN reason TEXT")
        db.commit()
    # Runtime migration: create inventory tables for existing databases
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'inventory_sessions' not in tables:
        db.execute("""CREATE TABLE inventory_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL, completed_at TEXT,
            user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','completed')))""")
        db.execute("""CREATE TABLE inventory_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL REFERENCES inventory_sessions(id) ON DELETE CASCADE,
            product_id INTEGER NOT NULL REFERENCES products(id),
            expected_qty REAL NOT NULL, actual_qty REAL,
            delta REAL, reason TEXT, price REAL)""")
        db.commit()
    db.close()


# ── Jinja filter: UTC string → local timezone ──────────────
@app.template_filter('localdt')
def local_dt_filter(value, fmt='%d.%m.%Y %H:%M'):
    if not value:
        return '—'
    tz_name = get_setting('timezone', 'UTC')
    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        logger.error("Unknown timezone in settings: %r", tz_name)
        tz = ZoneInfo('UTC')
    try:
        dt = datetime.strptime(str(value)[:19], '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
        return dt.astimezone(tz).strftime(fmt)
    except (ValueError, TypeError):
        return str(value)[:16]

# ── Context processor ──────────────────────────────────────
@app.context_processor
def inject_globals():
    return dict(app_tz=get_setting('timezone', 'UTC'))

# ── Auth guard (replaces @login_required on every route) ───
PUBLIC = {'login', 'setup', 'static'}

@app.before_request
def check_auth():
    if request.endpoint is None:
        return
    if request.endpoint in PUBLIC:
        return
    db = get_db()
    user_count = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    if user_count == 0:
        return redirect(url_for('setup'))
    if not current_user.is_authenticated:
        return redirect(url_for('login', next=request.path))


# ── Auth routes ────────────────────────────────────────────
@app.route('/setup', methods=['GET', 'POST'])
def setup():
    db = get_db()
    if db.execute("SELECT COUNT(*) FROM users").fetchone()[0] > 0:
        return redirect(url_for('stock'))
    error = None
    if request.method == 'POST':
        username  = request.form.get('username', '').strip()
        password  = request.form.get('password', '')
        password2 = request.form.get('password2', '')
        if not username or not password:
            error = 'Введите логин и пароль'
        elif password != password2:
            error = 'Пароли не совпадают'
        elif len(password) < 12:
            error = 'Пароль должен содержать не менее 12 символов'
        elif not re.search(r'[A-Z]', password):
            error = 'Пароль должен содержать хотя бы одну заглавную букву'
        elif not re.search(r'[a-z]', password):
            error = 'Пароль должен содержать хотя бы одну строчную букву'
        elif not re.search(r'\d', password):
            error = 'Пароль должен содержать хотя бы одну цифру'
        elif not re.search(r'[!@#$%^&*()_+\-=\[\]{};\'\":,.<>?/\\|`~]', password):
            error = 'Пароль должен содержать хотя бы один специальный символ'
        else:
            now = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
            db.execute(
                "INSERT INTO users (username, password_hash, role, theme, created_at) VALUES (?,?,?,?,?)",
                (username, generate_password_hash(password), 'admin', 'dark', now)
            )
            # Default timezone
            set_setting(db, 'timezone', 'UTC')
            db.commit()
            flash('Администратор создан. Войдите в систему.')
            return redirect(url_for('login'))
    return render_template('setup.html', error=error)

@app.route('/login', methods=['GET', 'POST'])
def login():
    db = get_db()
    if db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
        return redirect(url_for('setup'))
    if current_user.is_authenticated:
        return redirect(url_for('stock'))
    error = None
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        row = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if row and check_password_hash(row['password_hash'], password):
            login_user(User(row), remember=True)
            next_url = request.args.get('next', '')
            parsed = urllib.parse.urlparse(next_url)
            # Allow only relative same-origin paths
            if next_url and not parsed.scheme and not parsed.netloc and next_url.startswith('/'):
                return redirect(next_url)
            return redirect(url_for('stock'))
        error = 'Неверный логин или пароль'
    return render_template('login.html', error=error)

@app.route('/logout', methods=['POST'])
def logout():
    logout_user()
    return redirect(url_for('login'))


# ── Settings ───────────────────────────────────────────────
COMMON_TIMEZONES = [
    ('UTC',                    'UTC'),
    ('Europe/Moscow',          'Москва (UTC+3)'),
    ('Europe/Minsk',           'Минск (UTC+3)'),
    ('Europe/Kyiv',            'Киев (UTC+2/3)'),
    ('Europe/Istanbul',        'Стамбул (UTC+3)'),
    ('Europe/London',          'Лондон (UTC+0/1)'),
    ('Europe/Berlin',          'Берлин (UTC+1/2)'),
    ('Europe/Paris',           'Париж (UTC+1/2)'),
    ('Asia/Dubai',             'Дубай (UTC+4)'),
    ('Asia/Almaty',            'Алматы (UTC+5)'),
    ('Asia/Tashkent',          'Ташкент (UTC+5)'),
    ('Asia/Yekaterinburg',     'Екатеринбург (UTC+5)'),
    ('Asia/Novosibirsk',       'Новосибирск (UTC+7)'),
    ('Asia/Krasnoyarsk',       'Красноярск (UTC+7)'),
    ('Asia/Irkutsk',           'Иркутск (UTC+8)'),
    ('Asia/Yakutsk',           'Якутск (UTC+9)'),
    ('Asia/Vladivostok',       'Владивосток (UTC+10)'),
    ('Asia/Magadan',           'Магадан (UTC+11)'),
    ('America/New_York',       'Нью-Йорк (UTC-5/-4)'),
    ('America/Chicago',        'Чикаго (UTC-6/-5)'),
    ('America/Los_Angeles',    'Лос-Анджелес (UTC-8/-7)'),
]

@app.route('/settings', methods=['GET', 'POST'])
def settings():
    db = get_db()
    if request.method == 'POST':
        action = request.form.get('action', '')

        if action == 'timezone':
            tz = request.form.get('timezone', 'UTC')
            set_setting(db, 'timezone', tz)
            db.commit()
            flash('Часовой пояс сохранён')

        elif action == 'theme':
            theme = request.form.get('theme', 'system')
            if theme in ('dark', 'light', 'system'):
                db.execute("UPDATE users SET theme=? WHERE id=?", (theme, current_user.id))
                db.commit()
            flash('Тема сохранена')

        elif action == 'add_user' and current_user.role == 'admin':
            username = request.form.get('username', '').strip()
            password = request.form.get('password', '')
            role     = request.form.get('role', 'user')
            if username and password:
                try:
                    now = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
                    db.execute(
                        "INSERT INTO users (username, password_hash, role, theme, created_at) VALUES (?,?,?,?,?)",
                        (username, generate_password_hash(password), role, 'dark', now)
                    )
                    db.commit()
                    flash(f'Пользователь «{username}» добавлен')
                except IntegrityError as e:
                    logger.warning("add_user duplicate: %s", e)
                    flash('Пользователь с таким именем уже существует')
            else:
                flash('Введите логин и пароль')

        elif action == 'delete_user' and current_user.role == 'admin':
            uid_raw = request.form.get('user_id', '')
            if not uid_raw or not uid_raw.strip().lstrip('-').isdigit():
                flash('Некорректный идентификатор пользователя')
            else:
                uid_int = int(uid_raw)
                if uid_int == current_user.id:
                    flash('Нельзя удалить самого себя')
                else:
                    db.execute("DELETE FROM users WHERE id=?", (uid_int,))
                    db.commit()
                    flash('Пользователь удалён')

        elif action == 'change_password':
            old = request.form.get('old_password', '')
            new = request.form.get('new_password', '')
            new2 = request.form.get('new_password2', '')
            row = db.execute("SELECT password_hash FROM users WHERE id=?", (current_user.id,)).fetchone()
            if not check_password_hash(row['password_hash'], old):
                flash('Неверный текущий пароль')
            elif new != new2:
                flash('Новые пароли не совпадают')
            elif len(new) < 4:
                flash('Пароль слишком короткий (минимум 4 символа)')
            else:
                db.execute("UPDATE users SET password_hash=? WHERE id=?",
                           (generate_password_hash(new), current_user.id))
                db.commit()
                flash('Пароль изменён')

        elif action == 'danger_stock' and current_user.role == 'admin':
            mode  = request.form.get('danger_stock_mode', 'recent')
            weeks = request.form.get('danger_stock_weeks', '2')
            if mode in ('recent', 'alltime') and weeks.isdigit() and 1 <= int(weeks) <= 52:
                set_setting(db, 'danger_stock_mode',  mode)
                set_setting(db, 'danger_stock_weeks', weeks)
                db.commit()
                flash('Настройки опасного остатка сохранены')
            else:
                flash('Некорректные значения')

        return redirect(url_for('settings'))

    tz_name      = get_setting('timezone', 'UTC')
    danger_mode  = get_setting('danger_stock_mode',  'recent')
    danger_weeks = get_setting('danger_stock_weeks', '2')
    users_list   = db.execute("SELECT * FROM users ORDER BY created_at").fetchall()
    user_theme   = db.execute("SELECT theme FROM users WHERE id=?",
                              (current_user.id,)).fetchone()['theme']
    return render_template('settings.html',
        tz_name=tz_name,
        timezones=COMMON_TIMEZONES,
        users=users_list,
        current_theme=user_theme,
        danger_mode=danger_mode,
        danger_weeks=int(danger_weeks),
    )


# ── FIFO helpers ───────────────────────────────────────────
def fifo_consume(db, product_id, qty, operation_item_id):
    lots = db.execute("""
        SELECT * FROM lots
        WHERE product_id=? AND remaining_qty > 0
        ORDER BY created_at ASC, id ASC
    """, (product_id,)).fetchall()
    remaining  = qty
    total_cost = 0.0
    for lot in lots:
        if remaining <= 0:
            break
        take = min(lot["remaining_qty"], remaining)
        total_cost += take * lot["price_per_unit"]
        remaining  -= take
        db.execute("UPDATE lots SET remaining_qty = remaining_qty - ? WHERE id=?", (take, lot["id"]))
        db.execute("INSERT INTO lot_consumptions (operation_item_id, lot_id, quantity) VALUES (?,?,?)",
                   (operation_item_id, lot["id"], take))
    return total_cost / qty if qty > 0 else None

def fifo_restore(db, operation_item_id):
    for c in db.execute("SELECT * FROM lot_consumptions WHERE operation_item_id=?",
                        (operation_item_id,)).fetchall():
        db.execute("UPDATE lots SET remaining_qty = remaining_qty + ? WHERE id=?",
                   (c["quantity"], c["lot_id"]))
    db.execute("DELETE FROM lot_consumptions WHERE operation_item_id=?", (operation_item_id,))

def fifo_add_lot(db, product_id, operation_item_id, price_per_unit, qty, created_at):
    if price_per_unit is None or price_per_unit <= 0:
        return
    db.execute("""
        INSERT INTO lots (product_id, operation_item_id, price_per_unit, original_qty, remaining_qty, created_at)
        VALUES (?,?,?,?,?,?)
    """, (product_id, operation_item_id, price_per_unit, qty, qty, created_at))

def fifo_remove_lot(db, operation_item_id):
    lot = db.execute("SELECT * FROM lots WHERE operation_item_id=?", (operation_item_id,)).fetchone()
    if not lot:
        return
    consumed = lot["original_qty"] - lot["remaining_qty"]
    if consumed > 0:
        db.execute("UPDATE lots SET remaining_qty=0 WHERE id=?", (lot["id"],))
    else:
        db.execute("DELETE FROM lots WHERE id=?", (lot["id"],))


# ── Root ───────────────────────────────────────────────────
@app.route("/")
def index():
    return redirect(url_for("stock"))

# ── Categories ─────────────────────────────────────────────
@app.route("/categories")
def categories():
    db = get_db()
    cats = db.execute("SELECT * FROM categories ORDER BY name").fetchall()
    return render_template("categories.html", categories=cats)

@app.route("/categories/create", methods=["POST"])
def create_category():
    db = get_db()
    db.execute("INSERT INTO categories (name, description) VALUES (?,?)",
               (request.form["name"], request.form.get("description") or None))
    db.commit()
    return redirect(url_for("categories"))

@app.route("/categories/<int:id>/update", methods=["POST"])
def update_category(id):
    db = get_db()
    db.execute("UPDATE categories SET name=?, description=? WHERE id=?",
               (request.form["name"], request.form.get("description") or None, id))
    db.commit()
    return redirect(url_for("categories"))

@app.route("/categories/<int:id>/delete", methods=["POST"])
def delete_category(id):
    db = get_db()
    db.execute("DELETE FROM categories WHERE id=?", (id,))
    db.commit()
    return redirect(url_for("categories"))

# ── Units ──────────────────────────────────────────────────
@app.route("/units")
def units():
    db = get_db()
    u = db.execute("SELECT * FROM units ORDER BY name").fetchall()
    return render_template("units.html", units=u)

@app.route("/units/create", methods=["POST"])
def create_unit():
    db = get_db()
    db.execute("INSERT INTO units (name, short_name) VALUES (?,?)",
               (request.form["name"], request.form["short_name"]))
    db.commit()
    return redirect(url_for("units"))

@app.route("/units/<int:id>/update", methods=["POST"])
def update_unit(id):
    db = get_db()
    db.execute("UPDATE units SET name=?, short_name=? WHERE id=?",
               (request.form["name"], request.form["short_name"], id))
    db.commit()
    return redirect(url_for("units"))

@app.route("/units/<int:id>/delete", methods=["POST"])
def delete_unit(id):
    db = get_db()
    db.execute("DELETE FROM units WHERE id=?", (id,))
    db.commit()
    return redirect(url_for("units"))


# ── Products ───────────────────────────────────────────────
@app.route("/products")
def products():
    db = get_db()
    cat_filter    = request.args.get("category_id", "")
    show_archived = request.args.get("show_archived", "0")
    if cat_filter:
        prods = db.execute("""
            SELECT p.*, c.name as cat_name, u.short_name as unit_short
            FROM products p
            LEFT JOIN categories c ON c.id = p.category_id
            LEFT JOIN units u ON u.id = p.unit_id
            WHERE p.category_id = ? AND (p.is_active = 1 OR ? = '1')
            ORDER BY p.is_active DESC, p.sort_order, p.name
        """, (cat_filter, show_archived)).fetchall()
    else:
        prods = db.execute("""
            SELECT p.*, c.name as cat_name, u.short_name as unit_short
            FROM products p
            LEFT JOIN categories c ON c.id = p.category_id
            LEFT JOIN units u ON u.id = p.unit_id
            WHERE p.is_active = 1 OR ? = '1'
            ORDER BY p.is_active DESC, p.sort_order, p.name
        """, (show_archived,)).fetchall()
    cats      = db.execute("SELECT * FROM categories ORDER BY name").fetchall()
    all_units = db.execute("SELECT * FROM units ORDER BY name").fetchall()
    return render_template("products.html", products=prods, categories=cats,
                           units=all_units, selected_category=cat_filter,
                           show_archived=show_archived)

@app.route("/products/create", methods=["POST"])
def create_product():
    db = get_db()
    db.execute("INSERT INTO products (name, category_id, unit_id, description) VALUES (?,?,?,?)",
               (request.form["name"],
                request.form.get("category_id") or None,
                request.form.get("unit_id") or None,
                request.form.get("description") or None))
    db.commit()
    return redirect(url_for("products"))

@app.route("/products/<int:id>/update", methods=["POST"])
def update_product(id):
    db = get_db()
    db.execute("UPDATE products SET name=?, category_id=?, unit_id=?, description=? WHERE id=?",
               (request.form["name"],
                request.form.get("category_id") or None,
                request.form.get("unit_id") or None,
                request.form.get("description") or None, id))
    db.commit()
    return redirect(url_for("products"))

@app.route("/products/<int:id>/delete", methods=["POST"])
def delete_product(id):
    db = get_db()
    db.execute("DELETE FROM products WHERE id=?", (id,))
    db.commit()
    return redirect(url_for("products"))

@app.route("/products/<int:id>/toggle_active", methods=["POST"])
def toggle_product_active(id):
    db = get_db()
    db.execute("UPDATE products SET is_active = 1 - is_active WHERE id=?", (id,))
    db.commit()
    return redirect(request.referrer or url_for("products"))

@app.route("/products/reorder", methods=["POST"])
def reorder_products():
    order = request.json.get("order", [])   # list of product ids in new order
    db = get_db()
    for i, pid in enumerate(order):
        db.execute("UPDATE products SET sort_order=? WHERE id=?", (i, pid))
    db.commit()
    return {"ok": True}


# ── Operations: new ────────────────────────────────────────
@app.route("/operations/new")
def new_operation():
    db = get_db()
    prods = db.execute("""
        SELECT p.*, u.short_name as unit_short FROM products p
        LEFT JOIN units u ON u.id = p.unit_id
        WHERE p.is_active = 1 ORDER BY p.sort_order, p.name
    """).fetchall()
    stock_map = {str(p["id"]): float(p["current_stock"]) for p in prods}
    return render_template("operation_new.html", products=prods, stock_map=stock_map)

@app.route("/operations/create", methods=["POST"])
def create_operation():
    db   = get_db()
    op_type = request.form.get("type", "IN")
    comment = request.form.get("comment") or None
    dt_str  = request.form.get("created_at", "")
    if dt_str:
        try:
            created_at = datetime.strptime(dt_str, "%Y-%m-%dT%H:%M").strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            created_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    else:
        created_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    product_ids = request.form.getlist("product_id[]")
    quantities  = request.form.getlist("quantity[]")
    prices      = request.form.getlist("price_per_unit[]")

    items = []
    for i, pid in enumerate(product_ids):
        if not pid or i >= len(quantities) or not quantities[i]:
            continue
        raw_qty = float(quantities[i])
        qty = int(raw_qty)  # enforce integer quantity
        if qty < 1:
            continue
        price = float(prices[i]) if i < len(prices) and prices[i] else None
        items.append((int(pid), qty, price))

    if items:
        cur   = db.execute("INSERT INTO operations (type, created_at, comment, user_id) VALUES (?,?,?,?)",
                           (op_type, created_at, comment, current_user.id))
        op_id = cur.lastrowid
        for pid, qty, price in items:
            if op_type == "OUT":
                item_cur  = db.execute(
                    "INSERT INTO operation_items (operation_id, product_id, quantity, price_per_unit) VALUES (?,?,?,NULL)",
                    (op_id, pid, qty))
                item_id   = item_cur.lastrowid
                fifo_price = fifo_consume(db, pid, qty, item_id)
                if fifo_price is not None:
                    db.execute("UPDATE operation_items SET price_per_unit=? WHERE id=?",
                               (round(fifo_price, 6), item_id))
            else:
                item_cur = db.execute(
                    "INSERT INTO operation_items (operation_id, product_id, quantity, price_per_unit) VALUES (?,?,?,?)",
                    (op_id, pid, qty, price))
                item_id  = item_cur.lastrowid
                fifo_add_lot(db, pid, item_id, price, qty, created_at)
            if op_type == "IN":
                db.execute("UPDATE products SET current_stock = current_stock + ? WHERE id=?", (qty, pid))
            else:
                db.execute("UPDATE products SET current_stock = current_stock - ? WHERE id=?", (qty, pid))
        db.commit()
    return redirect(url_for("history"))


# ── Operations: edit / delete ──────────────────────────────
@app.route("/operations/<int:id>/edit")
def edit_operation(id):
    db = get_db()
    op = db.execute("SELECT * FROM operations WHERE id=?", (id,)).fetchone()
    if not op:
        return redirect(url_for("history"))
    items = db.execute("""
        SELECT oi.*, p.name as product_name, u.short_name as unit_short
        FROM operation_items oi
        JOIN products p ON p.id = oi.product_id
        LEFT JOIN units u ON u.id = p.unit_id
        WHERE oi.operation_id = ?
    """, (id,)).fetchall()
    prods     = db.execute("""
        SELECT p.*, u.short_name as unit_short FROM products p
        LEFT JOIN units u ON u.id = p.unit_id
        WHERE p.is_active = 1 ORDER BY p.sort_order, p.name
    """).fetchall()
    stock_map = {str(p["id"]): float(p["current_stock"]) for p in prods}
    return render_template("operation_edit.html", op=op, items=items,
                           products=prods, stock_map=stock_map)

@app.route("/operations/<int:id>/update", methods=["POST"])
def update_operation(id):
    db = get_db()
    op = db.execute("SELECT * FROM operations WHERE id=?", (id,)).fetchone()
    if not op:
        return redirect(url_for("history"))
    old_items = db.execute("SELECT * FROM operation_items WHERE operation_id=?", (id,)).fetchall()
    for item in old_items:
        if op["type"] == "IN":
            fifo_remove_lot(db, item["id"])
            db.execute("UPDATE products SET current_stock = current_stock - ? WHERE id=?",
                       (item["quantity"], item["product_id"]))
        else:
            fifo_restore(db, item["id"])
            db.execute("UPDATE products SET current_stock = current_stock + ? WHERE id=?",
                       (item["quantity"], item["product_id"]))
    db.execute("DELETE FROM operation_items WHERE operation_id=?", (id,))
    op_type = request.form.get("type", op["type"])
    comment = request.form.get("comment") or None
    dt_str  = request.form.get("created_at", "")
    if dt_str:
        try:
            created_at = datetime.strptime(dt_str, "%Y-%m-%dT%H:%M").strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            created_at = op["created_at"]
    else:
        created_at = op["created_at"]
    db.execute("UPDATE operations SET type=?, created_at=?, comment=? WHERE id=?",
               (op_type, created_at, comment, id))
    product_ids = request.form.getlist("product_id[]")
    quantities  = request.form.getlist("quantity[]")
    prices      = request.form.getlist("price_per_unit[]")
    for i, pid in enumerate(product_ids):
        if not pid or i >= len(quantities) or not quantities[i]:
            continue
        qty = int(float(quantities[i]))  # enforce integer quantity
        if qty < 1:
            continue
        price = float(prices[i]) if i < len(prices) and prices[i] else None
        if op_type == "OUT":
            item_cur  = db.execute(
                "INSERT INTO operation_items (operation_id, product_id, quantity, price_per_unit) VALUES (?,?,?,NULL)",
                (id, int(pid), qty))
            item_id   = item_cur.lastrowid
            fifo_price = fifo_consume(db, int(pid), qty, item_id)
            if fifo_price is not None:
                db.execute("UPDATE operation_items SET price_per_unit=? WHERE id=?",
                           (round(fifo_price, 6), item_id))
        else:
            item_cur = db.execute(
                "INSERT INTO operation_items (operation_id, product_id, quantity, price_per_unit) VALUES (?,?,?,?)",
                (id, int(pid), qty, price))
            item_id  = item_cur.lastrowid
            fifo_add_lot(db, int(pid), item_id, price, qty, created_at)
        if op_type == "IN":
            db.execute("UPDATE products SET current_stock = current_stock + ? WHERE id=?", (qty, int(pid)))
        else:
            db.execute("UPDATE products SET current_stock = current_stock - ? WHERE id=?", (qty, int(pid)))
    db.commit()
    return redirect(url_for("history"))

@app.route("/operations/<int:id>/delete", methods=["POST"])
def delete_operation(id):
    db = get_db()
    op = db.execute("SELECT * FROM operations WHERE id=?", (id,)).fetchone()
    if not op:
        return redirect(url_for("history"))
    for item in db.execute("SELECT * FROM operation_items WHERE operation_id=?", (id,)).fetchall():
        if op["type"] == "IN":
            fifo_remove_lot(db, item["id"])
            db.execute("UPDATE products SET current_stock = current_stock - ? WHERE id=?",
                       (item["quantity"], item["product_id"]))
        else:
            fifo_restore(db, item["id"])
            db.execute("UPDATE products SET current_stock = current_stock + ? WHERE id=?",
                       (item["quantity"], item["product_id"]))
    db.execute("DELETE FROM operations WHERE id=?", (id,))
    db.commit()
    return redirect(url_for("history"))


# ── Stats detail ───────────────────────────────────────────
@app.route("/stats/detail")
def stats_detail():
    db = get_db()
    product_id  = request.args.get("product_id", "")
    category_id = request.args.get("category_id", "")
    date_from   = request.args.get("date_from", "")
    date_to     = request.args.get("date_to", "")
    preset      = request.args.get("preset", "")

    now = datetime.utcnow()
    if preset == "today":
        date_from = now.strftime("%Y-%m-%d")
        date_to = now.strftime("%Y-%m-%d")
    elif preset == "week":
        date_from = (now - timedelta(days=7)).strftime("%Y-%m-%d")
        date_to = now.strftime("%Y-%m-%d")
    elif preset == "month":
        date_from = now.strftime("%Y-%m-01")
        date_to = now.strftime("%Y-%m-%d")
    elif preset == "year":
        date_from = (now - timedelta(days=365)).strftime("%Y-%m-%d")
        date_to = now.strftime("%Y-%m-%d")

    subject = subject_type = None
    if product_id:
        subject = db.execute("""
            SELECT p.*, c.name as cat_name, u.short_name as unit_short, u.name as unit_name
            FROM products p
            LEFT JOIN categories c ON c.id = p.category_id
            LEFT JOIN units u ON u.id = p.unit_id WHERE p.id=?
        """, (product_id,)).fetchone()
        subject_type = "product"
    elif category_id:
        subject = db.execute("SELECT * FROM categories WHERE id=?", (category_id,)).fetchone()
        subject_type = "category"
    if not subject:
        return redirect(url_for("stats"))

    op_conditions, op_params = ["1=1"], []
    if date_from:
        op_conditions.append("o.created_at >= ?"); op_params.append(date_from)
    if date_to:
        op_conditions.append("o.created_at <= ?"); op_params.append(date_to + " 23:59:59")
    op_where = " AND ".join(op_conditions)

    if subject_type == "product":
        prod_filter = "AND oi.product_id = ?"; prod_params = [int(product_id)]
    else:
        prod_filter = "AND p.category_id = ?"; prod_params = [int(category_id)]

    rows = db.execute(f"""
        SELECT p.id, p.name as product_name, u.short_name as unit_short, p.current_stock,
            COALESCE(SUM(CASE WHEN o.type='IN' THEN oi.quantity ELSE 0 END),0) as in_qty,
            COALESCE(SUM(CASE WHEN o.type='OUT' THEN oi.quantity ELSE 0 END),0) as out_qty,
            COALESCE(SUM(CASE WHEN o.type='IN' THEN oi.quantity*COALESCE(oi.price_per_unit,0) ELSE 0 END),0) as in_total,
            COALESCE(SUM(CASE WHEN o.type='OUT' THEN oi.quantity*COALESCE(oi.price_per_unit,0) ELSE 0 END),0) as out_total
        FROM products p
        LEFT JOIN units u ON u.id = p.unit_id
        LEFT JOIN operation_items oi ON oi.product_id = p.id {prod_filter}
        LEFT JOIN operations o ON o.id = oi.operation_id AND {op_where}
        WHERE 1=1 {prod_filter.replace('AND oi.product_id','AND p.id').replace('AND p.category_id','AND p.category_id')}
        GROUP BY p.id ORDER BY p.sort_order, p.name
    """, prod_params + op_params + prod_params).fetchall()

    ops_raw = db.execute(f"""
        SELECT DISTINCT o.* FROM operations o
        JOIN operation_items oi ON oi.operation_id = o.id
        JOIN products p ON p.id = oi.product_id
        WHERE {op_where} {prod_filter}
        ORDER BY o.created_at DESC LIMIT 50
    """, op_params + prod_params).fetchall()

    ops_with_items = []
    for op in ops_raw:
        items = db.execute("""
            SELECT oi.*, p.name as product_name, u.short_name as unit_short
            FROM operation_items oi JOIN products p ON p.id=oi.product_id
            LEFT JOIN units u ON u.id=p.unit_id WHERE oi.operation_id=?
        """, (op["id"],)).fetchall()
        ops_with_items.append({"op": op, "items": items})

    chart_data = db.execute(f"""
        SELECT strftime('%Y-%m', o.created_at) as month,
            SUM(CASE WHEN o.type='IN' THEN oi.quantity ELSE 0 END) as in_qty,
            SUM(CASE WHEN o.type='OUT' THEN oi.quantity ELSE 0 END) as out_qty,
            SUM(CASE WHEN o.type='IN' THEN oi.quantity*COALESCE(oi.price_per_unit,0) ELSE 0 END) as in_total,
            SUM(CASE WHEN o.type='OUT' THEN oi.quantity*COALESCE(oi.price_per_unit,0) ELSE 0 END) as out_total
        FROM operations o JOIN operation_items oi ON oi.operation_id=o.id
        JOIN products p ON p.id=oi.product_id
        WHERE o.created_at >= date('now','-12 months') {prod_filter}
        GROUP BY month ORDER BY month
    """, prod_params).fetchall()

    all_products   = db.execute("SELECT id, name FROM products WHERE is_active=1 ORDER BY sort_order, name").fetchall()
    all_categories = db.execute("SELECT id, name FROM categories ORDER BY name").fetchall()
    return render_template("stats_detail.html",
        subject=subject, subject_type=subject_type, rows=rows, ops=ops_with_items,
        chart_data=[dict(r) for r in chart_data],
        total_in_qty=sum(r["in_qty"] for r in rows),
        total_out_qty=sum(r["out_qty"] for r in rows),
        total_in_sum=sum(r["in_total"] for r in rows),
        total_out_sum=sum(r["out_total"] for r in rows),
        all_products=all_products, all_categories=all_categories,
        filters={"product_id": product_id, "category_id": category_id,
                 "date_from": date_from, "date_to": date_to, "preset": preset},
    )


# ── History ────────────────────────────────────────────────
@app.route("/history")
def history():
    db         = get_db()
    date_from  = request.args.get("date_from", "")
    date_to    = request.args.get("date_to", "")
    op_type    = request.args.get("op_type", "")
    product_id = request.args.get("product_id", "")
    user_id    = request.args.get("user_id", "")
    page       = int(request.args.get("page", 1))
    limit      = 20
    offset     = (page - 1) * limit

    conditions, params = [], []
    if date_from:
        conditions.append("o.created_at >= ?"); params.append(date_from)
    if date_to:
        conditions.append("o.created_at <= ?"); params.append(date_to + " 23:59:59")
    if op_type in ("IN", "OUT", "ADJUST"):
        conditions.append("o.type = ?"); params.append(op_type)
    if product_id:
        conditions.append("EXISTS (SELECT 1 FROM operation_items oi WHERE oi.operation_id=o.id AND oi.product_id=?)")
        params.append(int(product_id))
    if user_id:
        conditions.append("o.user_id = ?"); params.append(int(user_id))

    where  = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    total  = db.execute(f"SELECT COUNT(*) FROM operations o {where}", params).fetchone()[0]
    ops    = db.execute(
        f"""SELECT o.*, u.username as op_username
            FROM operations o
            LEFT JOIN users u ON u.id = o.user_id
            {where} ORDER BY o.created_at DESC LIMIT ? OFFSET ?""",
        params + [limit, offset]
    ).fetchall()

    ops_with_items = []
    for op in ops:
        items = db.execute("""
            SELECT oi.*, p.name as product_name, u.short_name as unit_short
            FROM operation_items oi JOIN products p ON p.id=oi.product_id
            LEFT JOIN units u ON u.id=p.unit_id WHERE oi.operation_id=?
        """, (op["id"],)).fetchall()
        ops_with_items.append({"op": op, "items": items})

    all_products = db.execute("SELECT id, name FROM products WHERE is_active=1 ORDER BY sort_order, name").fetchall()
    all_users    = db.execute("SELECT id, username FROM users ORDER BY username").fetchall()
    pages   = (total + limit - 1) // limit
    filters = {"date_from": date_from, "date_to": date_to,
               "op_type": op_type, "product_id": product_id, "user_id": user_id}
    return render_template("history.html", operations=ops_with_items, products=all_products,
                           users=all_users, total=total, page=page, pages=pages, filters=filters)

# ── Stats ──────────────────────────────────────────────────
@app.route("/stats")
def stats():
    db        = get_db()
    date_from = request.args.get("date_from", "")
    date_to   = request.args.get("date_to", "")
    preset    = request.args.get("preset", "")

    now = datetime.utcnow()
    if preset == "today":
        date_from = now.strftime("%Y-%m-%d")
        date_to = now.strftime("%Y-%m-%d")
    elif preset == "week":
        date_from = (now - timedelta(days=7)).strftime("%Y-%m-%d")
        date_to = now.strftime("%Y-%m-%d")
    elif preset == "month":
        date_from = now.strftime("%Y-%m-01")
        date_to = now.strftime("%Y-%m-%d")
    elif preset == "year":
        date_from = (now - timedelta(days=365)).strftime("%Y-%m-%d")
        date_to = now.strftime("%Y-%m-%d")

    op_conditions, op_params = [], []
    if date_from:
        op_conditions.append("o.created_at >= ?"); op_params.append(date_from)
    if date_to:
        op_conditions.append("o.created_at <= ?"); op_params.append(date_to + " 23:59:59")
    op_where = ("AND " + " AND ".join(op_conditions)) if op_conditions else ""

    rows = db.execute(f"""
        SELECT p.id, p.name as product_name, u.short_name as unit_short, p.current_stock,
            COALESCE(SUM(CASE WHEN o.type='IN' THEN oi.quantity ELSE 0 END),0) as in_qty,
            COALESCE(SUM(CASE WHEN o.type='OUT' THEN oi.quantity ELSE 0 END),0) as out_qty,
            COALESCE(SUM(CASE WHEN o.type='IN' THEN oi.quantity*COALESCE(oi.price_per_unit,0) ELSE 0 END),0) as in_total,
            COALESCE(SUM(CASE WHEN o.type='OUT' THEN oi.quantity*COALESCE(oi.price_per_unit,0) ELSE 0 END),0) as out_total
        FROM products p
        LEFT JOIN units u ON u.id=p.unit_id
        LEFT JOIN operation_items oi ON oi.product_id=p.id
        LEFT JOIN operations o ON o.id=oi.operation_id {op_where}
        GROUP BY p.id, p.name, u.short_name, p.current_stock ORDER BY p.sort_order, p.name
    """, op_params).fetchall()

    all_products   = db.execute("SELECT id, name FROM products WHERE is_active=1 ORDER BY sort_order, name").fetchall()
    all_categories = db.execute("SELECT id, name FROM categories ORDER BY name").fetchall()
    return render_template("stats.html", rows=rows,
        total_in_qty=sum(r["in_qty"] for r in rows),
        total_out_qty=sum(r["out_qty"] for r in rows),
        total_in_sum=sum(r["in_total"] for r in rows),
        total_out_sum=sum(r["out_total"] for r in rows),
        all_products=all_products, all_categories=all_categories,
        filters={"date_from": date_from, "date_to": date_to, "preset": preset})


# ── Stock ──────────────────────────────────────────────────
@app.route("/stock")
def stock():
    db          = get_db()
    category_id = request.args.get("category_id", "")
    search      = request.args.get("search", "").strip()
    show        = request.args.get("show", "all")

    where_parts, params = [], []
    if category_id:
        where_parts.append("p.category_id = ?"); params.append(category_id)
    if search:
        where_parts.append("p.name LIKE ?"); params.append(f"%{search}%")
    if show == "in_stock":
        where_parts.append("p.current_stock > 0")
    elif show == "out_of_stock":
        where_parts.append("p.current_stock <= 0")
    where_parts.append("p.is_active = 1")
    where = "WHERE " + " AND ".join(where_parts)

    products = db.execute(f"""
        SELECT p.*, c.name as cat_name, u.short_name as unit_short,
            (SELECT oi.price_per_unit FROM operation_items oi
             JOIN operations o ON o.id=oi.operation_id
             WHERE oi.product_id=p.id AND o.type='IN' AND oi.price_per_unit IS NOT NULL
             ORDER BY o.created_at DESC LIMIT 1) as last_price,
            (SELECT o.created_at FROM operation_items oi
             JOIN operations o ON o.id=oi.operation_id
             WHERE oi.product_id=p.id ORDER BY o.created_at DESC LIMIT 1) as last_op_date,
            COALESCE((SELECT SUM(oi.quantity) FROM operation_items oi
             JOIN operations o ON o.id=oi.operation_id
             WHERE oi.product_id=p.id AND o.type='IN'),0) as total_in,
            COALESCE((SELECT SUM(oi.quantity) FROM operation_items oi
             JOIN operations o ON o.id=oi.operation_id
             WHERE oi.product_id=p.id AND o.type='OUT'),0) as total_out
        FROM products p
        LEFT JOIN categories c ON c.id=p.category_id
        LEFT JOIN units u ON u.id=p.unit_id
        {where} ORDER BY p.sort_order, p.name
    """, params).fetchall()

    categories       = db.execute("SELECT * FROM categories ORDER BY name").fetchall()
    in_stock_count   = sum(1 for p in products if p["current_stock"] > 0)
    out_stock_count  = sum(1 for p in products if p["current_stock"] <= 0)
    total_stock_value = sum(
        p["current_stock"] * p["last_price"]
        for p in products if p["current_stock"] > 0 and p["last_price"]
    )
    danger_mode  = get_setting('danger_stock_mode',  'recent')
    danger_weeks = get_setting('danger_stock_weeks', '2')
    danger_thresholds = calc_danger_thresholds(db, danger_mode, danger_weeks)

    return render_template("stock.html",
        products=products, categories=categories,
        total_items=len(products),
        in_stock_count=in_stock_count,
        out_of_stock_count=out_stock_count,
        total_stock_value=total_stock_value,
        danger_thresholds=danger_thresholds,
        danger_weeks=int(danger_weeks),
        filters={"category_id": category_id, "search": search, "show": show})

@app.route("/stock/print")
def stock_print():
    db          = get_db()
    category_id = request.args.get("category_id", "")
    search      = request.args.get("search", "").strip()
    show        = request.args.get("show", "all")

    where_parts, params = [], []
    if category_id:
        where_parts.append("p.category_id = ?"); params.append(category_id)
    if search:
        where_parts.append("p.name LIKE ?"); params.append(f"%{search}%")
    if show == "in_stock":
        where_parts.append("p.current_stock > 0")
    elif show == "out_of_stock":
        where_parts.append("p.current_stock <= 0")
    where_parts.append("p.is_active = 1")
    where = "WHERE " + " AND ".join(where_parts)

    products = db.execute(f"""
        SELECT p.*, c.name as cat_name, u.short_name as unit_short
        FROM products p
        LEFT JOIN categories c ON c.id=p.category_id
        LEFT JOIN units u ON u.id=p.unit_id
        {where} ORDER BY p.sort_order, p.name
    """, params).fetchall()

    from datetime import datetime
    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    return render_template("stock_print.html", products=products, now=now,
                           filters={"category_id": category_id, "search": search, "show": show})


# ── Inventory ─────────────────────────────────────────────────────────────────

@app.route("/inventory")
def inventory():
    db = get_db()
    sessions = db.execute("""
        SELECT s.*, u.username
        FROM inventory_sessions s
        LEFT JOIN users u ON u.id = s.user_id
        ORDER BY s.created_at DESC
    """).fetchall()
    return render_template("inventory.html", sessions=sessions)


@app.route("/inventory/new", methods=["POST"])
def inventory_new():
    db  = get_db()
    now = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    # Only one draft per user allowed
    existing = db.execute(
        "SELECT id FROM inventory_sessions WHERE status='draft' AND user_id=?",
        (current_user.id,)
    ).fetchone()
    if existing:
        return redirect(url_for('inventory_session', id=existing['id']))
    cur = db.execute(
        "INSERT INTO inventory_sessions (created_at, user_id, status) VALUES (?,?,'draft')",
        (now, current_user.id)
    )
    session_id = cur.lastrowid
    # Snapshot current stock for all active products
    products = db.execute(
        "SELECT id, current_stock FROM products WHERE is_active=1 ORDER BY sort_order, name"
    ).fetchall()
    for p in products:
        # Last IN price for financial evaluation
        price_row = db.execute("""
            SELECT oi.price_per_unit FROM operation_items oi
            JOIN operations o ON o.id = oi.operation_id
            WHERE oi.product_id = ? AND o.type = 'IN' AND oi.price_per_unit IS NOT NULL
            ORDER BY o.created_at DESC LIMIT 1
        """, (p['id'],)).fetchone()
        price = price_row['price_per_unit'] if price_row else None
        db.execute(
            "INSERT INTO inventory_items (session_id, product_id, expected_qty, price) VALUES (?,?,?,?)",
            (session_id, p['id'], p['current_stock'], price)
        )
    db.commit()
    return redirect(url_for('inventory_session', id=session_id))


ADJUST_REASONS = [
    ('accounting', 'Ошибка учёта'),
    ('writeoff',   'Списание (физическая потеря)'),
    ('regrade',    'Пересортица'),
    ('theft',      'Кража'),
]

@app.route("/inventory/<int:id>", methods=["GET", "POST"])
def inventory_session(id):
    db = get_db()
    session = db.execute("SELECT * FROM inventory_sessions WHERE id=?", (id,)).fetchone()
    if not session:
        return "Сессия не найдена", 404

    if request.method == "POST" and session['status'] == 'draft':
        items_db = db.execute(
            "SELECT * FROM inventory_items WHERE session_id=?", (id,)
        ).fetchall()
        for item in items_db:
            actual_raw = request.form.get(f"actual_{item['id']}", "").strip()
            reason     = request.form.get(f"reason_{item['id']}", "")
            price_raw  = request.form.get(f"price_{item['id']}", "").strip()
            if actual_raw == "":
                continue
            try:
                actual = float(actual_raw)
            except ValueError:
                continue
            delta = actual - item['expected_qty']
            price = item['price']
            if price_raw:
                try:
                    price = float(price_raw)
                except ValueError:
                    pass
            db.execute("""
                UPDATE inventory_items
                SET actual_qty=?, delta=?, reason=?, price=?
                WHERE id=?
            """, (actual, delta, reason, price, item['id']))
        db.commit()

        save_action = request.form.get("save_action", "save")
        if save_action == "complete":
            # Run completion logic inline (can't redirect to POST-only route)
            items_to_apply = db.execute(
                "SELECT * FROM inventory_items WHERE session_id=? AND actual_qty IS NOT NULL AND delta != 0",
                (id,)
            ).fetchall()
            if items_to_apply:
                now = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
                cur = db.execute(
                    "INSERT INTO operations (type, created_at, comment, user_id) VALUES ('ADJUST',?,?,?)",
                    (now, f"Инвентаризация #{id}", current_user.id)
                )
                op_id = cur.lastrowid
                for item in items_to_apply:
                    db.execute(
                        "INSERT INTO operation_items (operation_id, product_id, quantity, price_per_unit, reason) VALUES (?,?,?,?,?)",
                        (op_id, item['product_id'], abs(item['delta']), item['price'] or 0, item['reason'])
                    )
                    db.execute(
                        "UPDATE products SET current_stock = current_stock + ? WHERE id=?",
                        (item['delta'], item['product_id'])
                    )
            now2 = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
            db.execute("UPDATE inventory_sessions SET status='completed', completed_at=? WHERE id=?", (now2, id))
            db.commit()
            flash(f"Инвентаризация завершена. Скорректировано позиций: {len(items_to_apply)}")
            return redirect(url_for('inventory'))

        flash("Данные сохранены")
        return redirect(url_for('inventory_session', id=id))

    items = db.execute("""
        SELECT ii.*, p.name as product_name, u.short_name as unit_short
        FROM inventory_items ii
        JOIN products p ON p.id = ii.product_id
        LEFT JOIN units u ON u.id = p.unit_id
        WHERE ii.session_id = ?
        ORDER BY p.sort_order, p.name
    """, (id,)).fetchall()
    return render_template("inventory_session.html",
                           session=session, items=items, reasons=ADJUST_REASONS)


@app.route("/inventory/<int:id>/complete", methods=["POST"])
def inventory_complete(id):
    db = get_db()
    session = db.execute("SELECT * FROM inventory_sessions WHERE id=?", (id,)).fetchone()
    if not session or session['status'] != 'draft':
        return redirect(url_for('inventory'))

    items = db.execute(
        "SELECT * FROM inventory_items WHERE session_id=? AND actual_qty IS NOT NULL AND delta != 0",
        (id,)
    ).fetchall()

    if items:
        now = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
        # One ADJUST operation per session
        cur = db.execute(
            "INSERT INTO operations (type, created_at, comment, user_id) VALUES ('ADJUST',?,?,?)",
            (now, f"Инвентаризация #{id}", current_user.id)
        )
        op_id = cur.lastrowid

        for item in items:
            delta = item['delta']
            price = item['price'] or 0
            db.execute(
                "INSERT INTO operation_items (operation_id, product_id, quantity, price_per_unit) VALUES (?,?,?,?)",
                (op_id, item['product_id'], abs(delta), price if delta < 0 else price)
            )
            # Atomically update stock
            db.execute(
                "UPDATE products SET current_stock = current_stock + ? WHERE id=?",
                (delta, item['product_id'])
            )

    now = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    db.execute(
        "UPDATE inventory_sessions SET status='completed', completed_at=? WHERE id=?",
        (now, id)
    )
    db.commit()
    flash(f"Инвентаризация завершена. Скорректировано позиций: {len(items)}")
    return redirect(url_for('inventory'))


@app.route("/inventory/<int:id>/delete", methods=["POST"])
def inventory_delete(id):
    db = get_db()
    db.execute("DELETE FROM inventory_sessions WHERE id=? AND status='draft'", (id,))
    db.commit()
    return redirect(url_for('inventory'))


if __name__ == "__main__":
    init_db()
    # Добавляем host='0.0.0.0'
    app.run(debug=True, host='0.0.0.0', port=5000)
