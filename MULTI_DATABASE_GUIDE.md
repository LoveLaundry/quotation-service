# Multi-Database Support Guide

## Overview

The Quotation Service now supports **three database backends** with automatic detection:

- **MongoDB** - Document database (NoSQL)
- **PostgreSQL** - Relational database (SQL)
- **SQLite** - File-based database (SQL)

## How It Works

### Automatic Detection

The system automatically detects which database to use based on the `DATABASE_URL` format in your `.env` file:

```
mongodb://...       → MongoDB
mongodb+srv://...   → MongoDB
postgresql://...    → PostgreSQL
postgresql+...      → PostgreSQL (with driver specification)
sqlite:///...       → SQLite
```

### Architecture

```
Application Layer (FastAPI)
         ↓
  Repository Pattern
         ↓
    ┌────┴────┬──────────┐
    ↓         ↓          ↓
 MongoDB  PostgreSQL  SQLite
```

All endpoints use the same interface, but the underlying implementation adapts to the database type.

## Configuration Examples

### MongoDB Setup

**.env file:**
```env
DATABASE_URL=mongodb://localhost:27017
MONGODB_DB_NAME=quotations_db
MONGODB_COLLECTION=quotations
```

**Features:**
- No schema initialization needed
- Uses string-based ObjectIds (e.g., `"507f1f77bcf86cd799439011"`)
- Flexible document structure
- Indexes created automatically

**Requirements:**
- MongoDB server running on localhost:27017
- Or MongoDB Atlas connection string: `mongodb+srv://user:pass@cluster.mongodb.net/`

### PostgreSQL Setup

**.env file:**
```env
DATABASE_URL=postgresql://username:password@localhost:5432/quotations
```

**Features:**
- Structured relational schema
- Uses integer IDs (1, 2, 3, ...)
- ACID transactions
- Requires schema initialization

**Requirements:**
- PostgreSQL server running
- Database created
- Run `python reset_db.py` to initialize schema

### SQLite Setup

**.env file:**
```env
DATABASE_URL=sqlite:///./quotations.db
```

**Features:**
- File-based, no server needed
- Uses integer IDs
- Perfect for development
- Requires schema initialization

**Requirements:**
- No installation needed (built into Python)
- Run `python reset_db.py` to initialize schema

## API Behavior Differences

### ID Field

**MongoDB:**
```json
{
  "id": "507f1f77bcf86cd799439011",
  "client_name": "Hotel ABC"
}
```

**PostgreSQL/SQLite:**
```json
{
  "id": 1,
  "client_name": "Hotel ABC"
}
```

### Creating Records

The same API call works for all databases:

```bash
POST /quotations
{
  "client_name": "Hotel XYZ",
  "quotation_title": "2026 Prices",
  "line_items": [...],
  "status": "draft"
}
```

**MongoDB Response:** Returns with string ID
**PostgreSQL/SQLite Response:** Returns with integer ID

### Querying Records

```bash
GET /quotations/1              # Works for PostgreSQL/SQLite
GET /quotations/507f1f...      # Works for MongoDB
```

The system automatically handles ID type conversion.

## Switching Databases

To switch from one database to another:

1. **Stop the server** (Ctrl+C)

2. **Update .env file** with new DATABASE_URL

3. **Initialize if needed:**
   - PostgreSQL/SQLite: Run `python reset_db.py`
   - MongoDB: No initialization needed

4. **Restart the server:**
   ```bash
   uvicorn src.quotation_service.main:app --reload
   ```

5. **Verify the change:**
   ```bash
   curl http://localhost:8000/
   ```
   
   Response will show:
   ```json
   {
     "message": "Quotation Service API",
     "version": "1.0.0",
     "database": "mongodb"  // or "postgresql" or "sqlite"
   }
   ```

## Testing

### Test Database Detection

```bash
python test_database_detection.py
```

This verifies that URL patterns are correctly identified.

### Test Current Configuration

```bash
python test_multi_database.py
```

This shows your current database configuration and provides setup instructions.

### Test API Endpoints

```bash
# Check root endpoint
curl http://localhost:8000/

# Create a quotation
curl -X POST http://localhost:8000/quotations \
  -H "Content-Type: application/json" \
  -d '{
    "client_name": "Test Hotel",
    "quotation_title": "Test Prices",
    "line_items": [{
      "item_name": "Bed Sheet",
      "category": "Linen",
      "unit_price": 125.0
    }],
    "status": "draft"
  }'

# List all quotations
curl http://localhost:8000/quotations

# Get specific quotation
curl http://localhost:8000/quotations/1  # or ObjectId for MongoDB
```

## Production Recommendations

### For Small to Medium Scale
- **SQLite** for single-instance deployments
- **PostgreSQL** for multi-instance deployments with moderate traffic

### For Large Scale or Cloud
- **MongoDB Atlas** for global distributed deployments
- **PostgreSQL** (AWS RDS, Azure Database) for relational data needs

### Development
- **SQLite** for local development (no setup required)
- **MongoDB** if your production uses MongoDB

## Files Modified

New files created:
- `src/quotation_service/config.py` - Database detection
- `src/quotation_service/repository.py` - Abstract interface
- `src/quotation_service/mongodb_repository.py` - MongoDB implementation
- `src/quotation_service/postgresql_repository.py` - PostgreSQL/SQLite implementation
- `src/quotation_service/repository_factory.py` - Factory pattern
- `test_database_detection.py` - Detection tests
- `test_multi_database.py` - Configuration helper

Modified files:
- `pyproject.toml` - Added pymongo and motor dependencies
- `src/quotation_service/main.py` - Uses repository pattern
- `src/quotation_service/schemas.py` - Supports both ID types
- `README.md` - Updated documentation

## Troubleshooting

### MongoDB Connection Issues

**Problem:** `pymongo.errors.ServerSelectionTimeoutError`

**Solution:**
- Ensure MongoDB is running: `mongod --version`
- Check connection string in .env
- For MongoDB Atlas, ensure IP whitelist is configured

### PostgreSQL Connection Issues

**Problem:** `psycopg.OperationalError: could not connect`

**Solution:**
- Verify PostgreSQL is running
- Check credentials in DATABASE_URL
- Ensure database exists: `createdb quotations`

### Import Errors

**Problem:** `ModuleNotFoundError: No module named 'pymongo'`

**Solution:**
```bash
uv sync  # Reinstall dependencies
```

## Questions?

Check the updated README.md or run:
```bash
python test_multi_database.py
```

For detailed API documentation, visit:
```
http://localhost:8000/docs
```
