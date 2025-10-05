#!/usr/bin/env python
"""
Migration script to populate update_count field for existing DN records.

This script counts the number of records in dn_record table for each DN
and updates the update_count field in the dn table accordingly.

Usage:
    python scripts/migrate_update_count.py [--dry-run]
"""

import argparse
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from sqlalchemy import func  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import DN, DNRecord  # noqa: E402
from app.utils.logging import logger  # noqa: E402


def migrate_update_count(dry_run: bool = False) -> dict:
    """
    Migrate update_count field for all existing DN records.
    
    Args:
        dry_run: If True, only show what would be updated without making changes
        
    Returns:
        Dictionary with migration statistics
    """
    db = SessionLocal()
    stats = {
        "total_dn": 0,
        "updated_dn": 0,
        "skipped_dn": 0,
        "total_records": 0,
        "errors": []
    }
    
    try:
        # Get count of records per DN
        record_counts = (
            db.query(
                DNRecord.dn_number,
                func.count(DNRecord.id).label("record_count")
            )
            .group_by(DNRecord.dn_number)
            .all()
        )
        
        logger.info(f"Found {len(record_counts)} DNs with records in dn_record table")
        
        # Create a mapping of dn_number to record count
        count_map = {row.dn_number: row.record_count for row in record_counts}
        stats["total_records"] = sum(count_map.values())
        
        # Get all DNs
        all_dns = db.query(DN).all()
        stats["total_dn"] = len(all_dns)
        
        logger.info(f"Processing {stats['total_dn']} DN records...")
        
        for dn in all_dns:
            try:
                expected_count = count_map.get(dn.dn_number, 0)
                current_count = dn.update_count or 0
                
                if current_count == expected_count:
                    stats["skipped_dn"] += 1
                    logger.debug(f"DN {dn.dn_number}: already correct (count={current_count})")
                    continue
                
                if dry_run:
                    logger.info(
                        f"[DRY RUN] Would update DN {dn.dn_number}: "
                        f"{current_count} -> {expected_count}"
                    )
                    stats["updated_dn"] += 1
                else:
                    dn.update_count = expected_count
                    db.add(dn)
                    stats["updated_dn"] += 1
                    logger.info(
                        f"Updated DN {dn.dn_number}: "
                        f"{current_count} -> {expected_count}"
                    )
                    
            except Exception as e:
                error_msg = f"Error processing DN {dn.dn_number}: {e}"
                logger.error(error_msg)
                stats["errors"].append(error_msg)
        
        if not dry_run:
            db.commit()
            logger.info("Migration committed successfully")
        else:
            logger.info("Dry run completed - no changes made")
            
    except Exception as e:
        logger.error(f"Migration failed: {e}", exc_info=True)
        db.rollback()
        stats["errors"].append(str(e))
        raise
    finally:
        db.close()
    
    return stats


def print_statistics(stats: dict) -> None:
    """Print migration statistics in a formatted way."""
    print("\n" + "="*60)
    print("Migration Statistics")
    print("="*60)
    print(f"Total DN records:           {stats['total_dn']}")
    print(f"Total dn_records:           {stats['total_records']}")
    print(f"DNs updated:                {stats['updated_dn']}")
    print(f"DNs skipped (no change):    {stats['skipped_dn']}")
    
    if stats['errors']:
        print(f"\nErrors encountered:         {len(stats['errors'])}")
        for error in stats['errors']:
            print(f"  - {error}")
    else:
        print("\nNo errors encountered ✓")
    print("="*60 + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="Migrate update_count field for existing DN records"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be updated without making changes"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging"
    )
    
    args = parser.parse_args()
    
    if args.verbose:
        import logging
        logging.getLogger("app").setLevel(logging.DEBUG)
    
    logger.info("Starting update_count migration...")
    
    if args.dry_run:
        logger.info("Running in DRY RUN mode - no changes will be made")
    
    try:
        stats = migrate_update_count(dry_run=args.dry_run)
        print_statistics(stats)
        
        if stats['errors']:
            logger.error("Migration completed with errors")
            sys.exit(1)
        else:
            logger.info("Migration completed successfully!")
            sys.exit(0)
            
    except Exception as e:
        logger.error(f"Migration failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
