import sqlite3
import tempfile
import unittest
from pathlib import Path

from werkzeug.security import check_password_hash

from app import app, init_db


class CalorieTrackerTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        app.config.update(
            TESTING=True,
            SECRET_KEY="test-session-key",
            DATABASE=str(Path(self.temporary_directory.name) / "test.sqlite3"),
        )
        init_db()
        self.client = app.test_client()

    def tearDown(self):
        self.temporary_directory.cleanup()

    def register(self, username="mira", email="mira@example.com", password="test-password-123"):
        return self.client.post(
            "/register",
            data={
                "username": username,
                "email": email,
                "password": password,
                "confirm_password": password,
            },
            follow_redirects=True,
        )

    def log_in(self, identity="mira", password="test-password-123"):
        return self.client.post(
            "/login",
            data={"identity": identity, "password": password},
            follow_redirects=True,
        )

    def add_oatmeal(self):
        return self.client.post(
            "/",
            data={
                "food": "Oatmeal",
                "label_calories": "150",
                "reference_size": "40",
                "unit": "g",
                "amount_eaten": "60",
            },
            follow_redirects=True,
        )

    def database_rows(self, query, parameters=()):
        with sqlite3.connect(app.config["DATABASE"]) as connection:
            return connection.execute(query, parameters).fetchall()

    def test_register_login_logout_and_password_hash(self):
        response = self.register()
        self.assertIn("Today’s Calories", response.get_data(as_text=True))
        password_hash = self.database_rows("SELECT password_hash FROM users")[0][0]
        self.assertNotEqual(password_hash, "test-password-123")
        self.assertTrue(check_password_hash(password_hash, "test-password-123"))

        logged_out = self.client.post("/logout", follow_redirects=True)
        self.assertIn("Welcome back", logged_out.get_data(as_text=True))
        logged_in = self.log_in(identity="mira@example.com")
        self.assertIn("Today’s Calories", logged_in.get_data(as_text=True))

    def test_duplicate_username_or_email_is_rejected(self):
        self.register()
        self.client.post("/logout")
        duplicate_username = self.register("mira", "another@example.com")
        self.assertIn("already registered", duplicate_username.get_data(as_text=True))
        self.client.post("/logout")
        duplicate_email = self.register("another", "mira@example.com")
        self.assertIn("already registered", duplicate_email.get_data(as_text=True))
        self.assertEqual(self.database_rows("SELECT COUNT(*) FROM users")[0][0], 1)

    def test_registration_requires_matching_password_confirmation(self):
        response = self.client.post(
            "/register",
            data={
                "username": "mira",
                "email": "mira@example.com",
                "password": "test-password-123",
                "confirm_password": "different-password",
            },
        )
        self.assertIn("do not match", response.get_data(as_text=True))
        self.assertEqual(self.database_rows("SELECT COUNT(*) FROM users")[0][0], 0)

    def test_incompatible_database_is_preserved_before_schema_creation(self):
        database_path = Path(self.temporary_directory.name) / "legacy.sqlite3"
        with sqlite3.connect(database_path) as connection:
            connection.execute(
                """
                CREATE TABLE food_entries (
                    id INTEGER PRIMARY KEY,
                    food TEXT NOT NULL,
                    calories_per_serving REAL NOT NULL,
                    servings REAL NOT NULL,
                    total_calories REAL NOT NULL,
                    entry_date TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "INSERT INTO food_entries VALUES (?, ?, ?, ?, ?, ?)",
                (1, "Old oatmeal", 150, 1, 150, "2026-09-30"),
            )

        app.config["DATABASE"] = str(database_path)
        init_db()
        backup_paths = list(database_path.parent.glob("legacy.backup-*.sqlite3"))
        self.assertEqual(len(backup_paths), 1)
        with sqlite3.connect(backup_paths[0]) as backup:
            old_entry = backup.execute("SELECT food FROM food_entries").fetchone()
        self.assertEqual(old_entry, ("Old oatmeal",))
        self.assertIn("user_id", {row[1] for row in self.database_rows("PRAGMA table_info(food_entries)")})

    def test_tracker_and_delete_require_login(self):
        tracker = self.client.get("/")
        delete = self.client.post("/delete/1")
        self.assertEqual(tracker.status_code, 302)
        self.assertIn("/login", tracker.headers["Location"])
        self.assertEqual(delete.status_code, 302)
        self.assertIn("/login", delete.headers["Location"])

    def test_unit_calculation_and_entries_persist_after_refresh(self):
        self.register()
        response = self.add_oatmeal()
        self.assertIn("225", response.get_data(as_text=True))
        self.assertIn("60.0 g eaten", response.get_data(as_text=True))

        refreshed = self.client.get("/")
        self.assertIn("Oatmeal", refreshed.get_data(as_text=True))
        saved = self.database_rows(
            "SELECT label_calories, reference_size, unit, amount_eaten, total_calories FROM food_entries"
        )[0]
        self.assertEqual(saved, (150.0, 40.0, "g", 60.0, 225.0))

    def test_other_account_cannot_view_or_delete_entry(self):
        self.register()
        self.add_oatmeal()
        entry_id, owner_id = self.database_rows("SELECT id, user_id FROM food_entries")[0]

        self.client.post("/logout")
        other_account = self.register("noah", "noah@example.com")
        self.assertNotIn("Oatmeal", other_account.get_data(as_text=True))
        self.client.post(f"/delete/{entry_id}", follow_redirects=True)
        row = self.database_rows("SELECT user_id, food FROM food_entries WHERE id = ?", (entry_id,))
        self.assertEqual(row, [(owner_id, "Oatmeal")])

        self.client.post("/logout")
        owner_page = self.log_in()
        self.assertIn("Oatmeal", owner_page.get_data(as_text=True))

    def test_invalid_and_zero_measurements_are_rejected(self):
        self.register()
        invalid_submissions = (
            {"label_calories": "0", "reference_size": "40", "amount_eaten": "60"},
            {"label_calories": "-1", "reference_size": "40", "amount_eaten": "60"},
            {"label_calories": "inf", "reference_size": "40", "amount_eaten": "60"},
            {"label_calories": "150", "reference_size": "0", "amount_eaten": "60"},
            {"label_calories": "150", "reference_size": "-40", "amount_eaten": "60"},
            {"label_calories": "150", "reference_size": "40", "amount_eaten": "0"},
            {"label_calories": "150", "reference_size": "40", "amount_eaten": "-60"},
            {"label_calories": "nan", "reference_size": "40", "amount_eaten": "60"},
            {"label_calories": "not-a-number", "reference_size": "40", "amount_eaten": "60"},
        )
        for values in invalid_submissions:
            response = self.client.post(
                "/",
                data={"food": "Invalid food", "unit": "g", **values},
            )
            self.assertEqual(response.status_code, 200)
            self.assertIn("role=\"alert\"", response.get_data(as_text=True))
        self.assertEqual(self.database_rows("SELECT COUNT(*) FROM food_entries")[0][0], 0)

    def test_owner_can_add_and_delete_entry(self):
        self.register()
        self.add_oatmeal()
        entry_id = self.database_rows("SELECT id FROM food_entries")[0][0]
        response = self.client.post(f"/delete/{entry_id}", follow_redirects=True)
        self.assertIn("Your food log is empty", response.get_data(as_text=True))
        self.assertEqual(self.database_rows("SELECT COUNT(*) FROM food_entries")[0][0], 0)


if __name__ == "__main__":
    unittest.main()