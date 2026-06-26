# ProjetoBarbearia

ProjetoBarbearia is a Django SaaS for barbershops, built as a multi-tenant
portfolio project with public scheduling, authenticated dashboard, financial
operations, inventory, coupons, loyalty, reviews, waitlist, LGPD-oriented terms,
and cloud deploy support.

## Portfolio Notice

This repository is public so the architecture and implementation can be
reviewed as part of my portfolio. It is not open source.

The code is proprietary and may not be copied, modified, deployed, resold, used
commercially, or used to run a competing/internal service without written
permission. See [LICENSE](LICENSE).

## Stack

- Django 6 with server-rendered templates.
- SQLite for local development and PostgreSQL through `DATABASE_URL` in release.
- WhiteNoise for static files.
- Optional Redis cache through `REDIS_URL`.
- Optional S3-compatible storage for media.
- Render deploy through `render.yaml`, `build.sh`, and Gunicorn.

## Release Notes

- Secrets must live only in environment variables.
- Local databases, uploads, generated static files, assistant traces, exports,
  and private notes are ignored by Git.
- A fresh deploy should run `python manage.py migrate` against an empty
  production database.
