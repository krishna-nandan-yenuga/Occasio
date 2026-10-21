"""FastAPI app factory."""

from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="Occasio")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
