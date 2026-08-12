from fastapi import FastAPI

app = FastAPI(title="Etsy Performance Employee")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
