# Gifted

Private two-person web app: shared standing lists, owner reactions, partner claims, and a given-item history so gifts are not repeated. Either person can add to either list. The list owner must never observe claims on their own items — not in the UI, not in payloads, not in counts, not in response size.

Product contract: [V1.md](V1.md). Read that before writing code.

## Status

Domain layer is in `domain/` (dataclasses, in-memory store, tests). The Django app is in `app/`: session login, Home doors, My list / Their list, Add item, owner react and "got it elsewhere", partner claim / unclaim / give (a confirmed hand-over that moves the gift to the owner's Received list), against SQLite. Deploys to Railway (below). Home badges are not in this slice.

## Setup

Python 3.14. Dependencies go in a project venv (not system Python, not Homebrew Django — there isn't a formula).

```
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python app/manage.py migrate
.venv/bin/python app/manage.py bootstrap_pair \
  --first-username sam --first-name Sam --first-email sam@example.com \
  --second-username kit --second-name Kit --second-email kit@example.com
.venv/bin/python app/manage.py runserver
```

Use the two real people's details. The command prompts for each password; `--first-password` / `--second-password` skip the prompt (handy for scripts, but they end up in shell history). Re-running with the same two usernames updates names, emails and passwords. V1 allows exactly one pair, so a different pair is refused once one is connected.

To rename someone later, edit `Person.name` (what the other person sees) and `User.first_name` in Django admin at `/admin/`. Admin needs a staff account: `.venv/bin/python app/manage.py createsuperuser`.

Log in with either username. Add-item URLs are `/my-list/add/` and `/their-list/add/` — the list is the screen you opened, not a picker.

## Deploy on Railway

Railway builds from GitHub and redeploys on every push to `main`. `railway.json` tells it to run `start.sh`, which applies migrations, collects static files and starts gunicorn. The SQLite file lives on a Railway volume so it survives deploys.

One-time setup in the Railway dashboard:

1. **New Project → Deploy from GitHub repo →** this repo, branch `main`. Railway reads `requirements.txt` and `.python-version`.
2. **Add a volume** to the service with mount path `/data`.
3. **Variables** on the service:
   - `DJANGO_DEBUG` = `0`
   - `DJANGO_SECRET_KEY` = a long random string. Make one with `python3 -c "import secrets; print(secrets.token_urlsafe(50))"`. Never commit it.
   - `DJANGO_DB_PATH` = `/data/db.sqlite3`
4. **Settings → Networking → Generate Domain.** The app allows that domain automatically (`RAILWAY_PUBLIC_DOMAIN`). For a custom domain later, also set `DJANGO_ALLOWED_HOSTS` to it.
5. **Deploy.** The logs should end with migrations applied and `Listening at: http://0.0.0.0:…`.
6. **Create the two accounts** on the running service, once. Install the Railway CLI, run `railway link` in this repo, then `railway ssh` and:
   ```
   cd app
   python manage.py bootstrap_pair \
     --first-username … --first-name … --first-email … \
     --second-username … --second-name … --second-email …
   python manage.py createsuperuser   # optional: you, for /admin/
   ```
   It prompts for each password. Send each tester their username and password privately.

After that, pushing to `main` deploys. Each deploy takes the app down for a few seconds while the volume moves to the new copy.

If the build fails on Python 3.14, change `.python-version` to `3.13`; the app also runs on 3.13.

To run production settings locally: `DJANGO_DEBUG=0 DJANGO_SECRET_KEY=local-test DJANGO_ALLOWED_HOSTS=localhost PORT=8000 sh start.sh`, then open http://localhost:8000 in Chrome or Firefox (cookies are https-only, which those browsers allow on localhost).

## Tests

```
.venv/bin/python -m unittest discover -s domain/tests -t . -v
.venv/bin/python app/manage.py test gifts
```

## Layout

```
gift-app/
  V1.md
  README.md
  domain/          # dataclasses + in-memory store + tests
  app/             # Django project (config + gifts)
```

## How this will be developed

1. Domain + tests first — tables, derived states, viewer projections, mutation rules, secrecy invariants.
2. One vertical slice end to end: add item (Django + templates + HTMX + SQLite).
3. Deploy early (Fly.io / Render / Railway) with two operator-created accounts.

## Stack

Python 3.14. Django + Django templates + HTMX (Alpine only if a control needs client-only state). SQLite. Session cookies, not JWT. Tailwind or plain CSS — no frontend build step. Two accounts created in Django admin; no signup flow.

Rejected for V1: FastAPI (later rewrite if the goal is API-first Python), React (résumé-shaped complexity; HTML/API will not care later), Go (second implementation of the same domain, not a second V1 backend).

## Do not add

- Groups, more than two people, Secret Santa
- In-app checkout or payments (outbound links only)
- React / Vue / Svelte SPA
- Go backend
- Public signup, email verification, password reset
- A `List` table, a `Claim` table, a `Reaction` table, or a stored `status` column on items
