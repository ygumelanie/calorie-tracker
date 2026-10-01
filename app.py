import math
import os
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime

from flask import Flask, redirect, render_template, request, session, url_for
from flask_login import LoginManager, UserMixin, current_user, login_required, login_user, logout_user
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY") or "development-only-change-this-secret-key"
app.config["DATABASE"] = os.path.join(app.root_path, "calorie_tracker.db")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

login_manager = LoginManager()
login_manager.login_view = "login"
login_manager.init_app(app)

ENTRY_COLUMNS = {
    "id",
    "user_id",
    "food",
    "label_calories",
    "reference_size",
    "unit",
    "amount_eaten",
    "total_calories",
    "entry_date",
}
USER_COLUMNS = {"id", "username", "email", "password_hash"}
ALLOWED_UNITS = {"serving", "g", "oz"}
MAX_GUEST_ENTRIES = 20


class User(UserMixin):
    def __init__(self, user_id, username, email, password_hash, first_name=""):
        self.id = str(user_id)
        self.username = username
        self.email = email
        self.password_hash = password_hash
        self.first_name = first_name


@contextmanager
def get_connection():
    connection = sqlite3.connect(app.config["DATABASE"])
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def has_compatible_schema(database_path):
    connection = sqlite3.connect(database_path)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = ?",
                ("table",),
            )
        }
        if "food_entries" in tables:
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(food_entries)")
            }
            if not ENTRY_COLUMNS.issubset(columns):
                return False
        if "users" in tables:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(users)")}
            if not USER_COLUMNS.issubset(columns):
                return False
        return True
    except sqlite3.DatabaseError:
        return False
    finally:
        connection.close()


def backup_incompatible_database(database_path):
    root, extension = os.path.splitext(database_path)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup_path = f"{root}.backup-{timestamp}{extension or '.db'}"

    source = sqlite3.connect(database_path)
    backup = sqlite3.connect(backup_path)
    try:
        source.backup(backup)
    finally:
        backup.close()
        source.close()

    os.remove(database_path)
    return backup_path


def init_db():
    database_path = app.config["DATABASE"]
    os.makedirs(os.path.dirname(os.path.abspath(database_path)), exist_ok=True)
    if os.path.exists(database_path) and not has_compatible_schema(database_path):
        backup_path = backup_incompatible_database(database_path)
        print(f"Incompatible database preserved as {backup_path}")

    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL COLLATE NOCASE UNIQUE,
                email TEXT NOT NULL COLLATE NOCASE UNIQUE,
                password_hash TEXT NOT NULL,
                first_name TEXT NOT NULL DEFAULT ''
            )
            """
        )
        user_columns = {row[1] for row in connection.execute("PRAGMA table_info(users)")}
        if "first_name" not in user_columns:
            connection.execute("ALTER TABLE users ADD COLUMN first_name TEXT NOT NULL DEFAULT ''")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS food_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                food TEXT NOT NULL,
                label_calories REAL NOT NULL,
                reference_size REAL NOT NULL,
                unit TEXT NOT NULL CHECK (unit IN ('serving', 'g', 'oz')),
                amount_eaten REAL NOT NULL,
                total_calories REAL NOT NULL,
                entry_date TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
            """
        )


def get_user_by_id(user_id):
    with get_connection() as connection:
        row = connection.execute(
            "SELECT id, username, email, password_hash, first_name FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
    if row is None:
        return None
    return User(
        row["id"], row["username"], row["email"], row["password_hash"], row["first_name"]
    )


@login_manager.user_loader
def load_user(user_id):
    try:
        return get_user_by_id(int(user_id))
    except (TypeError, ValueError):
        return None


def get_today_entries(user_id):
    today = date.today().isoformat()
    with get_connection() as connection:
        entries = connection.execute(
            "SELECT * FROM food_entries WHERE user_id = ? AND entry_date = ? ORDER BY id DESC",
            (user_id, today),
        ).fetchall()
        total = connection.execute(
            "SELECT COALESCE(SUM(total_calories), 0) FROM food_entries WHERE user_id = ? AND entry_date = ?",
            (user_id, today),
        ).fetchone()[0]
    return entries, total


def transfer_guest_entries(user_id):
    guest_entries = session.get("guest_entries", [])
    if not guest_entries:
        return

    with get_connection() as connection:
        connection.executemany(
            """
            INSERT INTO food_entries
                (user_id, food, label_calories, reference_size, unit,
                 amount_eaten, total_calories, entry_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    user_id,
                    entry["food"],
                    entry["label_calories"],
                    entry["reference_size"],
                    entry["unit"],
                    entry["amount_eaten"],
                    entry["total_calories"],
                    entry["entry_date"],
                )
                for entry in guest_entries
            ],
        )
    session.pop("guest_entries", None)


@app.route("/register", methods=["GET", "POST"])
def register():
    error = None
    first_name_value = request.form.get("first_name", "").strip()
    username_value = request.form.get("username", "").strip()
    email_value = request.form.get("email", "").strip().lower()

    if request.method == "POST":
        password = request.form.get("password", "")
        confirmation = request.form.get("confirm_password", "")
        if not first_name_value or not username_value or not email_value or not password or not confirmation:
            error = "Please complete every field."
        elif len(first_name_value) > 40:
            error = "Choose a first name with 40 characters or fewer."
        elif len(username_value) > 40:
            error = "Choose a username with 40 characters or fewer."
        elif "@" not in email_value or "." not in email_value.rsplit("@", 1)[-1]:
            error = "Enter a valid email address."
        elif len(password) < 8:
            error = "Your password must be at least 8 characters long."
        elif password != confirmation:
            error = "Those passwords do not match. Please try again."
        else:
            try:
                with get_connection() as connection:
                    cursor = connection.execute(
                        "INSERT INTO users (first_name, username, email, password_hash) VALUES (?, ?, ?, ?)",
                        (first_name_value, username_value, email_value, generate_password_hash(password)),
                    )
                    user_id = cursor.lastrowid
            except sqlite3.IntegrityError:
                error = "That username or email is already registered."
            else:
                login_user(get_user_by_id(user_id))
                transfer_guest_entries(user_id)
                return redirect(url_for("home"))

    return render_template(
        "register.html",
        error=error,
        first_name_value=first_name_value,
        username_value=username_value,
        email_value=email_value,
    )


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    identity_value = request.form.get("identity", "").strip()

    if request.method == "POST":
        password = request.form.get("password", "")
        with get_connection() as connection:
            row = connection.execute(
                """
                SELECT id, username, email, password_hash, first_name FROM users
                WHERE username = ? COLLATE NOCASE OR email = ? COLLATE NOCASE
                """,
                (identity_value, identity_value),
            ).fetchone()
        if row is not None and check_password_hash(row["password_hash"], password):
            login_user(
                User(
                    row["id"],
                    row["username"],
                    row["email"],
                    row["password_hash"],
                    row["first_name"],
                )
            )
            transfer_guest_entries(row["id"])
            return redirect(url_for("home"))
        error = "That username/email and password combination wasn't recognized."

    return render_template("login.html", error=error, identity_value=identity_value)


@app.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    return redirect(url_for("home"))


@app.route("/", methods=["GET", "POST"])
def home():
    error = None
    food_value = request.form.get("food", "").strip()
    label_calories_value = request.form.get("label_calories", "").strip()
    reference_size_value = request.form.get("reference_size", "").strip()
    unit_value = request.form.get("unit", "serving").strip()
    amount_eaten_value = request.form.get("amount_eaten", "").strip()

    if request.method == "POST":
        if not food_value or not label_calories_value or not reference_size_value or not amount_eaten_value:
            error = "Please complete the food and serving details."
        elif len(food_value) > 100:
            error = "Food names must be 100 characters or fewer."
        elif unit_value not in ALLOWED_UNITS:
            error = "Choose serving, grams (g), or ounces (oz) as the unit."
        else:
            try:
                label_calories = float(label_calories_value)
                reference_size = float(reference_size_value)
                amount_eaten = float(amount_eaten_value)
            except ValueError:
                error = "Calories, serving size, and amount eaten must be numbers."
            else:
                values = (label_calories, reference_size, amount_eaten)
                if not all(math.isfinite(value) and value > 0 for value in values):
                    error = "All numeric values must be finite and greater than zero."
                else:
                    total_calories = label_calories * amount_eaten / reference_size
                    if not math.isfinite(total_calories):
                        error = "Those values are too large. Please enter smaller numbers."
                    elif not current_user.is_authenticated and len(session.get("guest_entries", [])) >= MAX_GUEST_ENTRIES:
                        error = f"Guest mode is limited to {MAX_GUEST_ENTRIES} entries. Create an account to keep logging."
                    else:
                        entry_date = date.today().isoformat()
                        if current_user.is_authenticated:
                            with get_connection() as connection:
                                connection.execute(
                                    """
                                    INSERT INTO food_entries
                                        (user_id, food, label_calories, reference_size, unit,
                                         amount_eaten, total_calories, entry_date)
                                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                                    """,
                                    (
                                        current_user.id,
                                        food_value,
                                        label_calories,
                                        reference_size,
                                        unit_value,
                                        amount_eaten,
                                        total_calories,
                                        entry_date,
                                    ),
                                )
                        else:
                            guest_entries = session.setdefault("guest_entries", [])
                            guest_entries.append(
                                {
                                    "id": max((entry["id"] for entry in guest_entries), default=0) + 1,
                                    "food": food_value,
                                    "label_calories": label_calories,
                                    "reference_size": reference_size,
                                    "unit": unit_value,
                                    "amount_eaten": amount_eaten,
                                    "total_calories": total_calories,
                                    "entry_date": entry_date,
                                }
                            )
                            session.modified = True
                        return redirect(url_for("home"))

    if current_user.is_authenticated:
        entries, daily_total = get_today_entries(current_user.id)
    else:
        entries = [
            entry
            for entry in reversed(session.get("guest_entries", []))
            if entry["entry_date"] == date.today().isoformat()
        ]
        daily_total = sum(entry["total_calories"] for entry in entries)
    return render_template(
        "index.html",
        entries=entries,
        daily_total=daily_total,
        error=error,
        food_value=food_value,
        label_calories_value=label_calories_value,
        reference_size_value=reference_size_value,
        unit_value=unit_value,
        amount_eaten_value=amount_eaten_value,
        today=date.today(),
        guest_mode=not current_user.is_authenticated,
        first_name=(current_user.first_name or current_user.username)
        if current_user.is_authenticated
        else None,
    )


@app.route("/delete/<int:entry_id>", methods=["POST"])
def delete_entry(entry_id):
    if current_user.is_authenticated:
        with get_connection() as connection:
            connection.execute(
                "DELETE FROM food_entries WHERE id = ? AND user_id = ? AND entry_date = ?",
                (entry_id, current_user.id, date.today().isoformat()),
            )
    else:
        session["guest_entries"] = [
            entry for entry in session.get("guest_entries", []) if entry["id"] != entry_id
        ]
    return redirect(url_for("home"))


init_db()


if __name__ == "__main__":
    app.run(debug=True)