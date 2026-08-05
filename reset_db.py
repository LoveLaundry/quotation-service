import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    print("ERROR: DATABASE_URL not found in .env file")
    exit(1)

engine = create_engine(DATABASE_URL)

print("Dropping existing quotations table...")
with engine.connect() as conn:
    # SQLite doesn't support CASCADE, so just drop the table
    if "sqlite" in DATABASE_URL:
        conn.execute(text("DROP TABLE IF EXISTS quotations"))
    else:
        conn.execute(text("DROP TABLE IF EXISTS quotations CASCADE"))
    conn.commit()

print("Recreating tables with new schema...")
from src.quotation_service.database import Base
from src.quotation_service.models import Quotation

Base.metadata.create_all(bind=engine)

print("Database schema updated successfully!")
print("\nNew schema:")
print("  - id (integer, primary key)")
print("  - client_name (string, indexed)")
print("  - quotation_title (string, nullable)")
print("  - line_items (json array)")
print("  - status (string, default='draft')")
print("  - created_at (timestamp)")
print("  - updated_at (timestamp)")
