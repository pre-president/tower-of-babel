# Tower of Babel — Central Hub Server

FastAPI + PostgreSQL central hub for **Tower of Babel**.

## Local Development & Testing

```bash
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# Windows PowerShell: .\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
pytest -v
```

## Database Migrations (Alembic)

```bash
cp .env.example .env  # Fill in real credentials locally (never commit .env)
alembic upgrade head
```

## Production Run

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8100 --workers 1
```
