import math
import os
import sqlite3
from datetime import date

from flask import Flask, redirect, render_template, request, url_for

app = Flask(__name__)
app.config["DATABASE"] = os.path.join(app.root_path, "calorie_tracker.db")


def init_db():
    with sqlite3.connect(app.config["DATABASE"]) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS food_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                food TEXT NOT NULL,
                calories_per_serving REAL NOT NULL,
                servings REAL NOT NULL,
                total_calories REAL NOT NULL,
                entry_date TEXT NOT NULL
            )
            """
        )


def get_today_entries():
    today = date.today().isoformat()
    with sqlite3.connect(app.config["DATABASE"]) as connection:
        connection.row_factory = sqlite3.Row
        entries = connection.execute(
            "SELECT * FROM food_entries WHERE entry_date = ? ORDER BY id DESC",
            (today,),
        ).fetchall()
        total = connection.execute(
            "SELECT COALESCE(SUM(total_calories), 0) FROM food_entries WHERE entry_date = ?",
            (today,),
        ).fetchone()[0]
    return entries, total


@app.route("/", methods=["GET", "POST"])
def home():
    error = None
    food_value = request.form.get("food", "").strip()
    calories_value = request.form.get("calories", "").strip()
    servings_value = request.form.get("servings", "").strip()

    if request.method == "POST":
        if not food_value or not calories_value or not servings_value:
            error = "Please fill in the food name, calories, and servings."
        else:
            try:
                calories = float(calories_value)
                servings = float(servings_value)
            except ValueError:
                error = "Calories and servings must be numbers. Please check them and try again."
            else:
                if not math.isfinite(calories) or not math.isfinite(servings):
                    error = "Please enter a valid number for calories and servings."
                elif calories <= 0 or servings <= 0:
                    error = "Calories and servings must be greater than zero."
                else:
                    total_calories = calories * servings
                    if not math.isfinite(total_calories):
                        error = "Those values are too large. Please enter smaller numbers."
                    else:
                        with sqlite3.connect(app.config["DATABASE"]) as connection:
                            connection.execute(
                                """
                                INSERT INTO food_entries
                                    (food, calories_per_serving, servings, total_calories, entry_date)
                                VALUES (?, ?, ?, ?, ?)
                                """,
                                (
                                    food_value,
                                    calories,
                                    servings,
                                    total_calories,
                                    date.today().isoformat(),
                                ),
                            )
                        return redirect(url_for("home"))

    entries, daily_total = get_today_entries()
    return render_template(
        "index.html",
        entries=entries,
        daily_total=daily_total,
        error=error,
        food_value=food_value,
        calories_value=calories_value,
        servings_value=servings_value,
        today=date.today(),
    )


@app.route("/delete/<int:entry_id>", methods=["POST"])
def delete_entry(entry_id):
    with sqlite3.connect(app.config["DATABASE"]) as connection:
        connection.execute(
            "DELETE FROM food_entries WHERE id = ? AND entry_date = ?",
            (entry_id, date.today().isoformat()),
        )
    return redirect(url_for("home"))


init_db()


if __name__ == "__main__":
    app.run(debug=True)