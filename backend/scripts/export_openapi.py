"""Writes the backend's OpenAPI schema to a file, straight from the
Pydantic models and route definitions -- no running server needed.
This is the source the frontend generates its types from (see
frontend/package.json's generate-types script), so backend and frontend
can't silently drift: regenerating this after a models.py change is
what would surface it.

Run: python backend/scripts/export_openapi.py
"""
import json
from pathlib import Path

from app.main import app

if __name__ == "__main__":
    schema = app.openapi()
    out_path = Path(__file__).parent.parent / "openapi.json"
    out_path.write_text(json.dumps(schema, indent=2) + "\n")
    print(f"Wrote {out_path}")
