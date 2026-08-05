# Quotation Service API

FastAPI backend service for managing hotel laundry quotations for Love Laundry guest accounts system.

## Features

- ✅ Full CRUD operations for quotations
- ✅ Support for multiple line items per quotation
- ✅ Client/hotel-based organization
- ✅ Status tracking (draft, sent, accepted, archived)
- ✅ Timestamps for created_at and updated_at
- ✅ CORS enabled for frontend integration
- ✅ **Multi-database support**: MongoDB, PostgreSQL, or SQLite
- ✅ **Automatic database detection** from DATABASE_URL

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

The service automatically detects the database type from the `DATABASE_URL` format.

Create a `.env` file with one of the following configurations:

**Option 1: MongoDB**
```env
DATABASE_URL=mongodb://localhost:27017
MONGODB_DB_NAME=quotations_db        # Optional, defaults to quotations_db
MONGODB_COLLECTION=quotations        # Optional, defaults to quotations
```

**Option 2: PostgreSQL**
```env
DATABASE_URL=postgresql://user:password@localhost:5432/quotations
```

**Option 3: SQLite (Development)**
```env
DATABASE_URL=sqlite:///./quotations.db
```

The service will automatically:
- Detect the database type from the URL prefix
- Use the appropriate driver (pymongo, psycopg, or sqlite3)
- Handle ID types (ObjectId strings for MongoDB, integers for SQL databases)

### 3. Initialize Database

**For PostgreSQL/SQLite:**
```bash
python reset_db.py
```

**For MongoDB:**
No initialization needed! Collections and indexes are created automatically.

This will create the quotations table/collection with the correct schema.

### 4. Run the Server

```bash
uvicorn src.quotation_service.main:app --reload
```

The API will be available at `http://localhost:8000`

Check which database is active by visiting the root endpoint - it will show:
```json
{
  "message": "Quotation Service API",
  "version": "1.0.0",
  "database": "mongodb"  // or "postgresql" or "sqlite"
}
```

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
- **SQLAlchemy** - ORM for PostgreSQL/SQLite
- **PyMongo** - MongoDB driver
- **Pydantic** - Data validation
- **MongoDB/PostgreSQL/SQLite** - Multi-database support
- **uvicorn** - ASGI server

### Database Architecture

The service uses a **repository pattern** to abstract database operations:

```
┌─────────────────────────────────────────────────────┐
│                   FastAPI Endpoints                 │
└────────────────────┬────────────────────────────────┘
                     │
            ┌────────▼────────┐
            │   Repository    │
            │    Factory      │
            └────────┬────────┘
                     │
        ┌────────────┼────────────┐
        │            │            │
   ┌────▼───┐  ┌────▼────┐  ┌────▼────┐
   │ MongoDB│  │PostgreSQL│  │ SQLite  │
   │  Repo  │  │   Repo   │  │  Repo   │
   └────────┘  └──────────┘  └─────────┘
```

This allows seamless switching between databases without changing application code.

## Migration from Old Schema

If you're migrating from an old schema, see [MIGRATION.md](./MIGRATION.md) for detailed instructions.

## Testing Multi-Database Support

Run the database detection test:
```bash
python test_database_detection.py
```

Check current database configuration:
```bash
python test_multi_database.py
```

Switch databases by updating `.env` and restarting the server. All API endpoints work identically regardless of the backend database.

**Note:** MongoDB uses string-based ObjectIds (e.g., `"507f1f77bcf86cd799439011"`), while PostgreSQL/SQLite use integer IDs (e.g., `1`, `2`, `3`). The API handles both transparently.
