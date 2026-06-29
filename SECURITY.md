# Security Notes

This project uses live cloud databases, API keys, and deployment automation. Treat every value in `.env`, GitHub Actions secrets, Azure credentials, VM login credentials, and frontend deploy tokens as private.

## Rules

- Never commit `.env`, database passwords, VM IP/passwords, SSH keys, API keys, personal access tokens, database dumps, or generated dependency folders.
- Keep `.env` permissions private: `chmod 600 .env`.
- Store production secrets in GitHub Actions Secrets, Azure Key Vault, or the hosting provider secret manager.
- Set `API_CORS_ORIGINS` to explicit frontend origins before deploying `api/main.py`.
- Set `ADMIN_TOKEN` before exposing `scripts/admin_dashboard.py` beyond localhost.
- Rotate credentials immediately if they were committed, pasted into logs, or shared with an AI tool.

## Current Required Secrets

- `POSTGRES_HOST`
- `POSTGRES_PORT`
- `POSTGRES_DB`
- `POSTGRES_USER`
- `POSTGRES_PASSWORD`
- `OPENAQ_KEYS`
- `FRONTEND_PAT`
- `ADMIN_TOKEN` for exposed admin deployments

## If A Secret Was Committed

Removing the secret from the latest files is not enough because Git history may still contain it.

1. Rotate the leaked credential in the provider dashboard.
2. Update local `.env` and GitHub Actions Secrets.
3. Purge history with `git filter-repo` or BFG only after coordinating with anyone who cloned the repo.
4. Force-push only when you understand the impact on collaborators.
