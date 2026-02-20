import sqlite3
from datetime import datetime, timedelta, timezone
from flask import Flask, render_template, request, redirect, url_for, g, flash

app = Flask(__name__)
app.secret_key = 'your-secret-key-here'  # Change this in production
DB_PATH = "warehouse.db"

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


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.execute("PRAGMA foreign_keys = ON")
    db.executescript("""
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
        current_stock REAL NOT NULL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS operations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        type TEXT NOT NULL CHECK(type IN ('IN','OUT')),
        created_at TEXT NOT NULL,
        comment TEXT
    );
    CREATE TABLE IF NOT EXISTS operation_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        operation_id INTEGER NOT NULL REFERENCES operations(id) ON DELETE CASCADE,
        product_id INTEGER NOT NULL REFERENCES products(id),
        quantity REAL NOT NULL,
        price_per_unit REAL
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
    """)
    db.commit()
    db.close()


# ── FIFO helpers ───────────────────────────────────────────
def fifo_consume(db, product_id, qty, operation_item_id, created_at):
    """Consume qty from oldest lots FIFO. Returns weighted avg price per unit or None."""
    lots = db.execute("""
        SELECT * FROM lots
        WHERE product_id=? AND remaining_qty > 0
        ORDER BY created_at ASC, id ASC
    """, (product_id,)).fetchall()

    remaining = qty
    total_cost = 0.0
    for lot in lots:
        if remaining <= 0:
            break
        take = min(lot["remaining_qty"], remaining)
        total_cost += take * lot["price_per_unit"]
        remaining -= take
        db.execute("UPDATE lots SET remaining_qty = remaining_qty - ? WHERE id=?",
                   (take, lot["id"]))
        db.execute("INSERT INTO lot_consumptions (operation_item_id, lot_id, quantity) VALUES (?,?,?)",
                   (operation_item_id, lot["id"], take))

    if qty > 0:
        return total_cost / qty
    return None


def fifo_restore(db, operation_item_id):
    """Restore lots consumed by an operation_item (for delete/edit rollback)."""
    consumptions = db.execute(
        "SELECT * FROM lot_consumptions WHERE operation_item_id=?", (operation_item_id,)
    ).fetchall()
    for c in consumptions:
        db.execute("UPDATE lots SET remaining_qty = remaining_qty + ? WHERE id=?",
                   (c["quantity"], c["lot_id"]))
    db.execute("DELETE FROM lot_consumptions WHERE operation_item_id=?", (operation_item_id,))


def fifo_add_lot(db, product_id, operation_item_id, price_per_unit, qty, created_at):
    """Create a new lot when goods arrive (IN operation)."""
    if price_per_unit is None or price_per_unit <= 0:
        return
    db.execute("""
        INSERT INTO lots (product_id, operation_item_id, price_per_unit, original_qty, remaining_qty, created_at)
        VALUES (?,?,?,?,?,?)
    """, (product_id, operation_item_id, price_per_unit, qty, qty, created_at))


def fifo_remove_lot(db, operation_item_id):
    """Remove lot created by an IN operation_item (for delete/edit rollback).
    Only safe if nothing has been consumed from it yet — otherwise restore partial."""
    lot = db.execute("SELECT * FROM lots WHERE operation_item_id=?", (operation_item_id,)).fetchone()
    if not lot:
        return
    consumed = lot["original_qty"] - lot["remaining_qty"]
    if consumed > 0:
        # Lot was partially consumed — just zero out the remaining
        db.execute("UPDATE lots SET remaining_qty=0 WHERE id=?", (lot["id"],))
    else:
        db.execute("DELETE FROM lots WHERE id=?", (lot["id"],))


# ── Root redirect ──────────────────────────────────────────
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
    units = db.execute("SELECT * FROM units ORDER BY name").fetchall()
    return render_template("units.html", units=units)


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
    cat_filter = request.args.get("category_id", "")
    if cat_filter:
        prods = db.execute("""
            SELECT p.*, c.name as cat_name, u.short_name as unit_short
            FROM products p
            LEFT JOIN categories c ON c.id = p.category_id
            LEFT JOIN units u ON u.id = p.unit_id
            WHERE p.category_id = ?
            ORDER BY p.name
        """, (cat_filter,)).fetchall()
    else:
        prods = db.execute("""
            SELECT p.*, c.name as cat_name, u.short_name as unit_short
            FROM products p
            LEFT JOIN categories c ON c.id = p.category_id
            LEFT JOIN units u ON u.id = p.unit_id
            ORDER BY p.name
        """).fetchall()
    cats = db.execute("SELECT * FROM categories ORDER BY name").fetchall()
    all_units = db.execute("SELECT * FROM units ORDER BY name").fetchall()
    return render_template("products.html", products=prods, categories=cats,
                           units=all_units, selected_category=cat_filter)


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
                request.form.get("description") or None,
                id))
    db.commit()
    return redirect(url_for("products"))


@app.route("/products/<int:id>/delete", methods=["POST"])
def delete_product(id):
    db = get_db()
    db.execute("DELETE FROM products WHERE id=?", (id,))
    db.commit()
    return redirect(url_for("products"))


# ── Operations ─────────────────────────────────────────────
@app.route("/operations/new")
def new_operation():
    db = get_db()
    prods = db.execute("""
        SELECT p.*, u.short_name as unit_short FROM products p
        LEFT JOIN units u ON u.id = p.unit_id ORDER BY p.name
    """).fetchall()
    # Stock map for frontend validation: {id: current_stock}
    stock_map = {str(p["id"]): float(p["current_stock"]) for p in prods}
    return render_template("operation_new.html", products=prods, stock_map=stock_map)


@app.route("/operations/create", methods=["POST"])
def create_operation():
    db = get_db()
    op_type = request.form.get("type", "IN")
    comment = request.form.get("comment") or None
    dt_str = request.form.get("created_at", "")
    if dt_str:
        try:
            created_at = datetime.strptime(dt_str, "%Y-%m-%dT%H:%M").strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            created_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    else:
        created_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    product_ids = request.form.getlist("product_id[]")
    quantities = request.form.getlist("quantity[]")
    prices = request.form.getlist("price_per_unit[]")

    items = []
    for i, pid in enumerate(product_ids):
        if not pid or i >= len(quantities) or not quantities[i]:
            continue
        qty = float(quantities[i])
        price = float(prices[i]) if i < len(prices) and prices[i] else None
        items.append((int(pid), qty, price))

    if items:
        cur = db.execute(
            "INSERT INTO operations (type, created_at, comment) VALUES (?,?,?)",
            (op_type, created_at, comment)
        )
        op_id = cur.lastrowid
        for pid, qty, price in items:
            if op_type == "OUT":
                # Insert item first to get its id, then FIFO consume
                item_cur = db.execute(
                    "INSERT INTO operation_items (operation_id, product_id, quantity, price_per_unit) VALUES (?,?,?,NULL)",
                    (op_id, pid, qty)
                )
                item_id = item_cur.lastrowid
                fifo_price = fifo_consume(db, pid, qty, item_id, created_at)
                if fifo_price is not None:
                    db.execute("UPDATE operation_items SET price_per_unit=? WHERE id=?",
                               (round(fifo_price, 6), item_id))
            else:
                item_cur = db.execute(
                    "INSERT INTO operation_items (operation_id, product_id, quantity, price_per_unit) VALUES (?,?,?,?)",
                    (op_id, pid, qty, price)
                )
                item_id = item_cur.lastrowid
                fifo_add_lot(db, pid, item_id, price, qty, created_at)

            if op_type == "IN":
                db.execute("UPDATE products SET current_stock = current_stock + ? WHERE id=?", (qty, pid))
            else:
                db.execute("UPDATE products SET current_stock = current_stock - ? WHERE id=?", (qty, pid))
        db.commit()

    return redirect(url_for("history"))


# ── Edit / Delete operation ────────────────────────────────
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
    prods = db.execute("""
        SELECT p.*, u.short_name as unit_short FROM products p
        LEFT JOIN units u ON u.id = p.unit_id ORDER BY p.name
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

    # Revert old stock changes and lots
    old_items = db.execute(
        "SELECT * FROM operation_items WHERE operation_id=?", (id,)
    ).fetchall()
    for item in old_items:
        if op["type"] == "IN":
            fifo_remove_lot(db, item["id"])
            db.execute("UPDATE products SET current_stock = current_stock - ? WHERE id=?",
                       (item["quantity"], item["product_id"]))
        else:
            fifo_restore(db, item["id"])
            db.execute("UPDATE products SET current_stock = current_stock + ? WHERE id=?",
                       (item["quantity"], item["product_id"]))

    # Delete old items
    db.execute("DELETE FROM operation_items WHERE operation_id=?", (id,))

    # Update operation meta
    op_type = request.form.get("type", op["type"])
    comment = request.form.get("comment") or None
    dt_str = request.form.get("created_at", "")
    if dt_str:
        try:
            created_at = datetime.strptime(dt_str, "%Y-%m-%dT%H:%M").strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            created_at = op["created_at"]
    else:
        created_at = op["created_at"]

    db.execute("UPDATE operations SET type=?, created_at=?, comment=? WHERE id=?",
               (op_type, created_at, comment, id))

    # Insert new items and update stock
    product_ids = request.form.getlist("product_id[]")
    quantities = request.form.getlist("quantity[]")
    prices = request.form.getlist("price_per_unit[]")

    for i, pid in enumerate(product_ids):
        if not pid or i >= len(quantities) or not quantities[i]:
            continue
        qty = float(quantities[i])
        price = float(prices[i]) if i < len(prices) and prices[i] else None
        if op_type == "OUT":
            item_cur = db.execute(
                "INSERT INTO operation_items (operation_id, product_id, quantity, price_per_unit) VALUES (?,?,?,NULL)",
                (id, int(pid), qty)
            )
            item_id = item_cur.lastrowid
            fifo_price = fifo_consume(db, int(pid), qty, item_id, created_at)
            if fifo_price is not None:
                db.execute("UPDATE operation_items SET price_per_unit=? WHERE id=?",
                           (round(fifo_price, 6), item_id))
        else:
            item_cur = db.execute(
                "INSERT INTO operation_items (operation_id, product_id, quantity, price_per_unit) VALUES (?,?,?,?)",
                (id, int(pid), qty, price)
            )
            item_id = item_cur.lastrowid
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

    # Revert stock and lots
    items = db.execute(
        "SELECT * FROM operation_items WHERE operation_id=?", (id,)
    ).fetchall()
    for item in items:
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


# ── Stats detail: by product or category ──────────────────
@app.route("/stats/detail")
def stats_detail():
    db = get_db()
    product_id = request.args.get("product_id", "")
    category_id = request.args.get("category_id", "")
    date_from = request.args.get("date_from", "")
    date_to = request.args.get("date_to", "")
    preset = request.args.get("preset", "")

    # Apply preset periods
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

    # Determine subject
    subject = None
    subject_type = None
    if product_id:
        subject = db.execute("""
            SELECT p.*, c.name as cat_name, u.short_name as unit_short, u.name as unit_name
            FROM products p
            LEFT JOIN categories c ON c.id = p.category_id
            LEFT JOIN units u ON u.id = p.unit_id
            WHERE p.id = ?
        """, (product_id,)).fetchone()
        subject_type = "product"
    elif category_id:
        subject = db.execute("SELECT * FROM categories WHERE id=?", (category_id,)).fetchone()
        subject_type = "category"

    if not subject:
        return redirect(url_for("stats"))

    # Build date filters
    op_conditions = ["1=1"]
    op_params = []
    if date_from:
        op_conditions.append("o.created_at >= ?")
        op_params.append(date_from)
    if date_to:
        op_conditions.append("o.created_at <= ?")
        op_params.append(date_to + " 23:59:59")
    op_where = " AND ".join(op_conditions)

    # Product filter
    if subject_type == "product":
        prod_filter = "AND oi.product_id = ?"
        prod_params = [int(product_id)]
    else:
        prod_filter = "AND p.category_id = ?"
        prod_params = [int(category_id)]

    # Summary per product
    rows = db.execute(f"""
        SELECT
            p.id, p.name as product_name, u.short_name as unit_short,
            p.current_stock,
            COALESCE(SUM(CASE WHEN o.type='IN' THEN oi.quantity ELSE 0 END), 0) as in_qty,
            COALESCE(SUM(CASE WHEN o.type='OUT' THEN oi.quantity ELSE 0 END), 0) as out_qty,
            COALESCE(SUM(CASE WHEN o.type='IN' THEN oi.quantity * COALESCE(oi.price_per_unit,0) ELSE 0 END), 0) as in_total,
            COALESCE(SUM(CASE WHEN o.type='OUT' THEN oi.quantity * COALESCE(oi.price_per_unit,0) ELSE 0 END), 0) as out_total
        FROM products p
        LEFT JOIN units u ON u.id = p.unit_id
        LEFT JOIN operation_items oi ON oi.product_id = p.id {prod_filter}
        LEFT JOIN operations o ON o.id = oi.operation_id AND {op_where}
        WHERE 1=1 {prod_filter.replace('AND oi.product_id', 'AND p.id').replace('AND p.category_id', 'AND p.category_id')}
        GROUP BY p.id, p.name, u.short_name, p.current_stock
        ORDER BY p.name
    """, prod_params + op_params + prod_params).fetchall()

    # Operations list (recent 50)
    ops_raw = db.execute(f"""
        SELECT DISTINCT o.*
        FROM operations o
        JOIN operation_items oi ON oi.operation_id = o.id
        JOIN products p ON p.id = oi.product_id
        WHERE {op_where} {prod_filter}
        ORDER BY o.created_at DESC
        LIMIT 50
    """, op_params + prod_params).fetchall()

    ops_with_items = []
    for op in ops_raw:
        items = db.execute("""
            SELECT oi.*, p.name as product_name, u.short_name as unit_short
            FROM operation_items oi
            JOIN products p ON p.id = oi.product_id
            LEFT JOIN units u ON u.id = p.unit_id
            WHERE oi.operation_id = ?
        """, (op["id"],)).fetchall()
        ops_with_items.append({"op": op, "items": items})

    # Monthly chart data (last 12 months)
    chart_data = db.execute(f"""
        SELECT
            strftime('%Y-%m', o.created_at) as month,
            SUM(CASE WHEN o.type='IN' THEN oi.quantity ELSE 0 END) as in_qty,
            SUM(CASE WHEN o.type='OUT' THEN oi.quantity ELSE 0 END) as out_qty
        FROM operations o
        JOIN operation_items oi ON oi.operation_id = o.id
        JOIN products p ON p.id = oi.product_id
        WHERE o.created_at >= date('now', '-12 months') {prod_filter}
        GROUP BY month
        ORDER BY month
    """, prod_params).fetchall()

    total_in_qty  = sum(r["in_qty"]   for r in rows)
    total_out_qty = sum(r["out_qty"]  for r in rows)
    total_in_sum  = sum(r["in_total"] for r in rows)
    total_out_sum = sum(r["out_total"] for r in rows)

    all_products   = db.execute("SELECT id, name FROM products ORDER BY name").fetchall()
    all_categories = db.execute("SELECT id, name FROM categories ORDER BY name").fetchall()

    return render_template("stats_detail.html",
        subject=subject,
        subject_type=subject_type,
        rows=rows,
        ops=ops_with_items,
        chart_data=[dict(r) for r in chart_data],
        total_in_qty=total_in_qty,
        total_out_qty=total_out_qty,
        total_in_sum=total_in_sum,
        total_out_sum=total_out_sum,
        all_products=all_products,
        all_categories=all_categories,
        filters={"product_id": product_id, "category_id": category_id,
                 "date_from": date_from, "date_to": date_to, "preset": preset},
    )


# ── History ────────────────────────────────────────────────
@app.route("/history")
def history():
    db = get_db()
    date_from = request.args.get("date_from", "")
    date_to = request.args.get("date_to", "")
    op_type = request.args.get("op_type", "")
    product_id = request.args.get("product_id", "")
    page = int(request.args.get("page", 1))
    limit = 20
    offset = (page - 1) * limit

    conditions = []
    params = []
    if date_from:
        conditions.append("o.created_at >= ?")
        params.append(date_from)
    if date_to:
        conditions.append("o.created_at <= ?")
        params.append(date_to + " 23:59:59")
    if op_type in ("IN", "OUT"):
        conditions.append("o.type = ?")
        params.append(op_type)
    if product_id:
        conditions.append("EXISTS (SELECT 1 FROM operation_items oi WHERE oi.operation_id=o.id AND oi.product_id=?)")
        params.append(int(product_id))

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    total = db.execute(f"SELECT COUNT(*) FROM operations o {where}", params).fetchone()[0]
    ops = db.execute(
        f"SELECT * FROM operations o {where} ORDER BY o.created_at DESC LIMIT ? OFFSET ?",
        params + [limit, offset]
    ).fetchall()

    ops_with_items = []
    for op in ops:
        items = db.execute("""
            SELECT oi.*, p.name as product_name, u.short_name as unit_short
            FROM operation_items oi
            JOIN products p ON p.id = oi.product_id
            LEFT JOIN units u ON u.id = p.unit_id
            WHERE oi.operation_id = ?
        """, (op["id"],)).fetchall()
        ops_with_items.append({"op": op, "items": items})

    all_products = db.execute("SELECT id, name FROM products ORDER BY name").fetchall()
    pages = (total + limit - 1) // limit
    filters = {"date_from": date_from, "date_to": date_to, "op_type": op_type, "product_id": product_id}
    return render_template("history.html",
        operations=ops_with_items,
        products=all_products,
        total=total, page=page, pages=pages,
        filters=filters,
    )


# ── Stats ──────────────────────────────────────────────────
@app.route("/stats")
def stats():
    db = get_db()
    date_from = request.args.get("date_from", "")
    date_to = request.args.get("date_to", "")
    preset = request.args.get("preset", "")

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

    op_conditions = []
    op_params = []
    if date_from:
        op_conditions.append("o.created_at >= ?")
        op_params.append(date_from)
    if date_to:
        op_conditions.append("o.created_at <= ?")
        op_params.append(date_to + " 23:59:59")
    op_where = ("AND " + " AND ".join(op_conditions)) if op_conditions else ""

    rows = db.execute(f"""
        SELECT
            p.id, p.name as product_name, u.short_name as unit_short,
            p.current_stock,
            COALESCE(SUM(CASE WHEN o.type='IN' THEN oi.quantity ELSE 0 END), 0) as in_qty,
            COALESCE(SUM(CASE WHEN o.type='OUT' THEN oi.quantity ELSE 0 END), 0) as out_qty,
            COALESCE(SUM(CASE WHEN o.type='IN' THEN oi.quantity * COALESCE(oi.price_per_unit,0) ELSE 0 END), 0) as in_total,
            COALESCE(SUM(CASE WHEN o.type='OUT' THEN oi.quantity * COALESCE(oi.price_per_unit,0) ELSE 0 END), 0) as out_total
        FROM products p
        LEFT JOIN units u ON u.id = p.unit_id
        LEFT JOIN operation_items oi ON oi.product_id = p.id
        LEFT JOIN operations o ON o.id = oi.operation_id {op_where}
        GROUP BY p.id, p.name, u.short_name, p.current_stock
        ORDER BY p.name
    """, op_params).fetchall()

    total_in_qty = sum(r["in_qty"] for r in rows)
    total_out_qty = sum(r["out_qty"] for r in rows)
    total_in_sum = sum(r["in_total"] for r in rows)
    total_out_sum = sum(r["out_total"] for r in rows)

    all_products   = db.execute("SELECT id, name FROM products ORDER BY name").fetchall()
    all_categories = db.execute("SELECT id, name FROM categories ORDER BY name").fetchall()

    return render_template("stats.html",
        rows=rows,
        total_in_qty=total_in_qty,
        total_out_qty=total_out_qty,
        total_in_sum=total_in_sum,
        total_out_sum=total_out_sum,
        all_products=all_products,
        all_categories=all_categories,
        filters={"date_from": date_from, "date_to": date_to, "preset": preset},
    )


# ── Stock (current inventory) ──────────────────────────────
@app.route("/stock")
def stock():
    db = get_db()
    category_id = request.args.get("category_id", "")
    search = request.args.get("search", "").strip()
    show = request.args.get("show", "all")  # all | in_stock | out_of_stock

    where_parts = []
    params = []

    if category_id:
        where_parts.append("p.category_id = ?")
        params.append(category_id)
    if search:
        where_parts.append("p.name LIKE ?")
        params.append(f"%{search}%")
    if show == "in_stock":
        where_parts.append("p.current_stock > 0")
    elif show == "out_of_stock":
        where_parts.append("p.current_stock <= 0")

    where = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

    products = db.execute(f"""
        SELECT
            p.*,
            c.name as cat_name,
            u.short_name as unit_short,
            -- last incoming price
            (SELECT oi.price_per_unit
             FROM operation_items oi
             JOIN operations o ON o.id = oi.operation_id
             WHERE oi.product_id = p.id AND o.type = 'IN' AND oi.price_per_unit IS NOT NULL
             ORDER BY o.created_at DESC LIMIT 1
            ) as last_price,
            -- last operation date
            (SELECT o.created_at
             FROM operation_items oi
             JOIN operations o ON o.id = oi.operation_id
             WHERE oi.product_id = p.id
             ORDER BY o.created_at DESC LIMIT 1
            ) as last_op_date,
            -- total received
            COALESCE((SELECT SUM(oi.quantity)
             FROM operation_items oi
             JOIN operations o ON o.id = oi.operation_id
             WHERE oi.product_id = p.id AND o.type = 'IN'), 0) as total_in,
            -- total issued
            COALESCE((SELECT SUM(oi.quantity)
             FROM operation_items oi
             JOIN operations o ON o.id = oi.operation_id
             WHERE oi.product_id = p.id AND o.type = 'OUT'), 0) as total_out
        FROM products p
        LEFT JOIN categories c ON c.id = p.category_id
        LEFT JOIN units u ON u.id = p.unit_id
        {where}
        ORDER BY p.name
    """, params).fetchall()

    categories = db.execute("SELECT * FROM categories ORDER BY name").fetchall()

    total_items = len(products)
    in_stock_count = sum(1 for p in products if p["current_stock"] > 0)
    out_of_stock_count = sum(1 for p in products if p["current_stock"] <= 0)
    total_stock_value = sum(
        (p["current_stock"] * p["last_price"])
        for p in products
        if p["current_stock"] > 0 and p["last_price"]
    )

    return render_template("stock.html",
        products=products,
        categories=categories,
        total_items=total_items,
        in_stock_count=in_stock_count,
        out_of_stock_count=out_of_stock_count,
        total_stock_value=total_stock_value,
        filters={"category_id": category_id, "search": search, "show": show},
    )


if __name__ == "__main__":
    init_db()
    app.run(debug=True, port=5000)
