# Gift app

Private two-person web app: shared standing lists, owner reactions, partner claims, and a given-item history so gifts are not repeated. Either person can add to either list. The list owner must never observe claims on their own items — not in the UI, not in payloads, not in counts, not in response size.

Product contract: [V1.md](V1.md). Read that before writing code.

## Status

Domain layer is in `domain/` (dataclasses, in-memory store, tests). The Django app is in `app/`: session login, Home doors, My list / Their list, Add item, owner react, and partner claim / unclaim / give against SQLite. Home badges / deploy are not in this slice.

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
