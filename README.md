# Etsy Performance Employee

本地运行的 Etsy 演出服数字员工应用，包含 Vue 3 前端与 FastAPI 后端。

## Prerequisites

- Node.js 24 and pnpm 11
- Python 3.11

## Install

```powershell
py -3.11 -m pip install -e ".\backend[dev]"
pnpm install
```

## Develop

Run both services from the project root:

```powershell
.\scripts\dev.ps1
```

The API health endpoint is `http://127.0.0.1:8765/api/health`; the frontend is `http://127.0.0.1:5173`.

## Verify

```powershell
Set-Location backend; py -3.11 -m pytest tests/test_health.py -q
Set-Location ..\frontend; pnpm vitest run src/App.spec.ts; pnpm build
```
