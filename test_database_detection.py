"""
Test script to verify database type detection
"""
import os
from src.quotation_service.config import detect_database_type, DatabaseType

# Test cases
test_cases = [
    ("mongodb://localhost:27017/test", DatabaseType.MONGODB),
    ("mongodb+srv://user:pass@cluster.mongodb.net/test", DatabaseType.MONGODB),
    ("postgresql://user:pass@localhost:5432/test", DatabaseType.POSTGRESQL),
    ("postgresql+psycopg://user:pass@localhost:5432/test", DatabaseType.POSTGRESQL),
    ("sqlite:///./test.db", DatabaseType.SQLITE),
]

print("Testing database type detection...\n")

all_passed = True
for url, expected_type in test_cases:
    detected = detect_database_type(url)
    status = "✓" if detected == expected_type else "✗"
    if detected != expected_type:
        all_passed = False
    print(f"{status} {url[:50]:<50} -> {detected.value} (expected: {expected_type.value})")

print("\n" + "="*80)
if all_passed:
    print("✓ All tests passed!")
else:
    print("✗ Some tests failed!")
print("="*80)
