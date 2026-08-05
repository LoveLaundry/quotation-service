# Quotation Service API

FastAPI backend service for managing hotel laundry quotations for Love Laundry guest accounts system.

## Features

- ✅ Full CRUD operations for quotations
- ✅ Support for multiple line items per quotation
- ✅ Client/hotel-based organization
- ✅ Status tracking (draft, sent, accepted, archived)
- ✅ Timestamps for created_at and updated_at
- ✅ CORS enabled for frontend integration

## Data Model

### Quotation
```json
{
  "id": 1,
  "client_name": "Nilawin Hotel",
  "quotation_title": "2026 Price List",
  "line_items": [
    {
      "item_name": "Bed Sheet",
      "category": "Bed Linen",
      "unit_price": 125.00,
      "notes": "(S, D)"
    }
  ],
  "status": "draft",
  "created_at": "2026-08-05T10:30:00Z",
  "updated_at": "2026-08-05T10:30:00Z"
}
```

## Quick Start

### 1. Install Dependencies

```bash
uv sync
```

### 2. Configure Database

Create a `.env` file:

```env
DATABASE_URL=postgresql://user:password@localhost:5432/quotations
```

For SQLite (development):
```env
DATABASE_URL=sqlite:///./quotations.db
```

### 3. Initialize Database

```bash
python reset_db.py
```

This will create the quotations table with the correct schema.

### 4. Run the Server

```bash
uvicorn src.quotation_service.main:app --reload
```

The API will be available at `http://localhost:8000`

### 5. Test the API

```bash
python test_api.py
```

Or visit the interactive docs at `http://localhost:8000/docs`

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | API information |
| GET | `/quotations` | List all quotations |
| GET | `/quotations/{id}` | Get single quotation |
| POST | `/quotations` | Create quotation |
| PUT | `/quotations/{id}` | Update quotation |
| DELETE | `/quotations/{id}` | Delete quotation |

## Example Usage

### Create Quotation

```bash
curl -X POST http://localhost:8000/quotations \
  -H "Content-Type: application/json" \
  -d '{
    "client_name": "Avenra Garden Hotel",
    "quotation_title": "2026 Annual Contract",
    "line_items": [
      {
        "item_name": "Bed Sheet",
        "category": "Bed Linen",
        "unit_price": 125.00,
        "notes": "Standard size"
      },
      {
        "item_name": "Bath Towel",
        "category": "Towels",
        "unit_price": 75.00
      }
    ],
    "status": "draft"
  }'
```

### Update Quotation

```bash
curl -X PUT http://localhost:8000/quotations/1 \
  -H "Content-Type: application/json" \
  -d '{
    "status": "sent"
  }'
```

## Frontend Integration

This API is designed to work with the React TypeScript frontend in `../quotations-ui`.

The response format matches the TypeScript interfaces defined in:
```
quotations-ui/src/types/quotation.ts
```

## Development

### Project Structure

```
quotation-service/
├── src/quotation_service/
│   ├── main.py          # FastAPI app and endpoints
│   ├── models.py        # SQLAlchemy models
│   ├── schemas.py       # Pydantic schemas
│   └── database.py      # Database configuration
├── reset_db.py          # Database reset script
├── test_api.py          # API test script
├── pyproject.toml       # Dependencies
└── .env                 # Environment variables
```

### Tech Stack

- **FastAPI** - Modern Python web framework
- **SQLAlchemy** - ORM for database operations
- **Pydantic** - Data validation
- **PostgreSQL/SQLite** - Database
- **uvicorn** - ASGI server

## Migration from Old Schema

If you're migrating from an old schema, see [MIGRATION.md](./MIGRATION.md) for detailed instructions.
