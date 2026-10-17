"""Запуск готового приложения: python run.py."""

from pathlib import Path

import uvicorn


if __name__ == "__main__":
    if not (Path(__file__).parent / "frontend/dist/index.html").exists():
        raise SystemExit(
            "Сначала соберите интерфейс: cd frontend → npm install → npm run build"
        )
    uvicorn.run("backend.main:app", host="127.0.0.1", port=8000)
