# FuelLog

FuelLog is a small Flask app for keeping a private daily food log. Each account can register, log in, enter food measurements, review today's calories, and delete its own entries.

## Run locally

From the project folder, create and activate a virtual environment, then install the dependencies:

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Set a private session key before starting the app:

```sh
export SECRET_KEY="replace-this-with-a-long-random-value"
python app.py
```

For local development only, the app has a clearly labeled fallback key when `SECRET_KEY` is not set. Do not use that fallback for a public deployment. Open <http://127.0.0.1:5000>, create an account, and start a food log.

## Food measurements

Enter the calories printed on a label, the label's reference serving size, its unit (`serving`, `g`, or `oz`), and the amount eaten. FuelLog calculates:

`label calories * amount eaten / reference serving size`

For example, 150 calories per 40 g and 60 g eaten is 225 calories.

## Data and safety

The app stores accounts and food entries in `calorie_tracker.db` using SQLite. Passwords are stored as Werkzeug password hashes. Food queries and deletes are scoped to the logged-in account. If startup finds an incompatible older database, it preserves a timestamped `.backup-*.db` copy before creating the new schema; entries from the old schema are not assigned to an account automatically.

The development server is intended for local use. `gunicorn` is listed as a dependency for a later deployment setup, but this project is not configured or deployed here.

## Run tests

```sh
python -m unittest discover -s tests
```