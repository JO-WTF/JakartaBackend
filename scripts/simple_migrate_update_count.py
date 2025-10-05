#!/usr/bin/env python
"""
Simple one-time migration to populate update_count for existing DNs.

Usage:
    python scripts/simple_migrate_update_count.py
"""

import os
import sys
from pathlib import Path

# Set required environment variables if not already set
os.environ.setdefault("DATABASE_URL", os.getenv("DATABASE_URL", ""))
os.environ.setdefault("STORAGE_DISK_PATH", "./data/uploads")

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from sqlalchemy import func  # noqa: E402
from app.db import SessionLocal, engine  # noqa: E402
from app.models import DN, DNRecord, Base  # noqa: E402

print("="*70)
print("DN Update Count Migration Script")
print("="*70)

# Ensure tables exist
print("\n1. Ensuring database schema is up to date...")
Base.metadata.create_all(bind=engine)
print("   ✓ Schema check complete")

# Create session
print("\n2. Connecting to database...")
db = SessionLocal()

try:
    # Count total DNs
    total_dns = db.query(func.count(DN.id)).scalar()
    print(f"   ✓ Found {total_dns} DN records")
    
    # Get record counts per DN
    print("\n3. Calculating record counts for each DN...")
    record_counts = (
        db.query(
            DNRecord.dn_number,
            func.count(DNRecord.id).label("count")
        )
        .group_by(DNRecord.dn_number)
        .all()
    )
    
    count_map = {row.dn_number: row.count for row in record_counts}
    total_records = sum(count_map.values())
    print(f"   ✓ Found {total_records} total records across {len(count_map)} DNs")
    
    # Preview changes
    print("\n4. Analyzing changes needed...")
    all_dns = db.query(DN).all()
    updates_needed = 0
    already_correct = 0
    
    for dn in all_dns:
        expected = count_map.get(dn.dn_number, 0)
        current = dn.update_count or 0
        if current != expected:
            updates_needed += 1
        else:
            already_correct += 1
    
    print(f"   • DNs needing update: {updates_needed}")
    print(f"   • DNs already correct: {already_correct}")
    
    if updates_needed == 0:
        print("\n✓ All DNs already have correct update_count values!")
        print("   No changes needed.")
        sys.exit(0)
    
    # Confirm before proceeding
    print(f"\n5. Ready to update {updates_needed} DN records")
    response = input("   Proceed with update? (yes/no): ").strip().lower()
    
    if response not in ["yes", "y"]:
        print("\n✗ Migration cancelled by user")
        sys.exit(0)
    
    # Perform updates
    print("\n6. Updating DN records...")
    updated_count = 0
    
    for dn in all_dns:
        expected = count_map.get(dn.dn_number, 0)
        current = dn.update_count or 0
        
        if current != expected:
            dn.update_count = expected
            db.add(dn)
            updated_count += 1
            
            if updated_count <= 10:  # Show first 10 updates
                print(f"   • {dn.dn_number}: {current} → {expected}")
            elif updated_count == 11:
                print(f"   • ... and {updates_needed - 10} more")
    
    # Commit changes
    print("\n7. Committing changes to database...")
    db.commit()
    print(f"   ✓ Successfully updated {updated_count} DN records")
    
    # Verify results
    print("\n8. Verifying results...")
    verification_query = """
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN d.update_count = COALESCE(r.count, 0) THEN 1 ELSE 0 END) as correct
        FROM dn d
        LEFT JOIN (
            SELECT dn_number, COUNT(*) as count
            FROM dn_record
            GROUP BY dn_number
        ) r ON d.dn_number = r.dn_number
    """
    result = db.execute(verification_query).fetchone()
    
    if result and result[0] == result[1]:
        print(f"   ✓ Verification passed: All {result[0]} DNs have correct update_count")
    else:
        print(f"   ⚠ Warning: {result[0] - result[1]} DNs still have mismatched counts")
    
    print("\n" + "="*70)
    print("Migration completed successfully!")
    print("="*70)
    
except Exception as e:
    print(f"\n✗ Error during migration: {e}")
    import traceback
    traceback.print_exc()
    db.rollback()
    sys.exit(1)
finally:
    db.close()
