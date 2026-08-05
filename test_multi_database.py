"""
Test script demonstrating the multi-database support.

This script shows how the quotation service automatically detects
and uses the correct database based on the DATABASE_URL.
"""

print("""
╔══════════════════════════════════════════════════════════════════════╗
║          Quotation Service - Multi-Database Support Test           ║
╚══════════════════════════════════════════════════════════════════════╝

The quotation service now supports multiple database backends:
  • MongoDB (document database)
  • PostgreSQL (relational database)
  • SQLite (local file database)

The system automatically detects the database type from DATABASE_URL:
  • mongodb:// or mongodb+srv://  → MongoDB
  • postgresql:// or postgresql+  → PostgreSQL
  • sqlite:///                    → SQLite

""")

import os
from dotenv import load_dotenv

load_dotenv()

current_db_url = os.getenv("DATABASE_URL", "Not set")
print("Current DATABASE_URL:")
print(f"  {current_db_url}")
print()

from src.quotation_service.config import DB_TYPE, DatabaseType

print(f"Detected Database Type: {DB_TYPE.value}")
print()

print("="*70)
print("To switch databases, update your .env file:")
print("="*70)
print()
print("For MongoDB:")
print("  DATABASE_URL=mongodb://localhost:27017")
print("  MONGODB_DB_NAME=quotations_db")
print("  MONGODB_COLLECTION=quotations")
print()
print("For PostgreSQL:")
print("  DATABASE_URL=postgresql://user:password@localhost:5432/dbname")
print()
print("For SQLite (current):")
print("  DATABASE_URL=sqlite:///./quotations.db")
print()
print("="*70)
print()

if DB_TYPE == DatabaseType.MONGODB:
    print("✓ MongoDB mode active")
    print("  - Document-based storage")
    print("  - String-based ObjectId")
    print("  - Flexible schema")
elif DB_TYPE == DatabaseType.POSTGRESQL:
    print("✓ PostgreSQL mode active")
    print("  - Relational storage")
    print("  - Integer-based ID")
    print("  - Structured schema")
elif DB_TYPE == DatabaseType.SQLITE:
    print("✓ SQLite mode active")
    print("  - File-based storage")
    print("  - Integer-based ID")
    print("  - Structured schema")

print()
print("Start the server with:")
print("  uvicorn src.quotation_service.main:app --reload")
print()
print("Then check the root endpoint:")
print("  http://127.0.0.1:8000/")
print()
