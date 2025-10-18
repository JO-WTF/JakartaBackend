"""Database schema migration utilities."""

from sqlalchemy import text, inspect, Table
from sqlalchemy.orm import Session

from app.utils.logging import logger
from app.models import Base


def log_migration_action(table: str, action: str, details: str | None = None) -> None:
    """Emit a structured log describing a concrete database change."""
    if details:
        logger.info("Migration action | table=%s | action=%s | %s", table, action, details)
    else:
        logger.info("Migration action | table=%s | action=%s", table, action)


def get_missing_columns(db: Session, table_name: str, model_table: Table) -> list[tuple[str, str]]:
    """Get columns that exist in the model but not in the database table."""
    try:
        inspector = inspect(db.bind)
        
        # Get existing columns from database
        existing_columns = {col['name'].lower() for col in inspector.get_columns(table_name)}
        
        # Get columns from SQLAlchemy model
        model_columns = {col.name.lower(): col for col in model_table.columns}
        
        # Find missing columns
        missing = []
        for col_name, col_obj in model_columns.items():
            if col_name not in existing_columns:
                # Generate column definition
                col_type = col_obj.type.compile(db.bind.dialect)
                
                # Handle nullable and default values
                nullable = "" if col_obj.nullable else " NOT NULL"
                default = ""
                
                # Handle default values properly
                if col_obj.default is not None:
                    if col_obj.default.is_scalar:
                        default_val = repr(col_obj.default.arg)
                        default = f" DEFAULT {default_val}"
                
                # Handle server_default values properly
                elif col_obj.server_default is not None:
                    try:
                        # Use dialect compiler to generate valid SQL
                        if hasattr(col_obj.server_default, 'arg'):
                            # For simple string values, use them directly
                            if isinstance(col_obj.server_default.arg, str):
                                default = f" DEFAULT '{col_obj.server_default.arg}'"
                            else:
                                # For complex expressions like func.now(), compile them
                                compiled_default = str(col_obj.server_default.arg.compile(
                                    dialect=db.bind.dialect,
                                    compile_kwargs={"literal_binds": True}
                                ))
                                default = f" DEFAULT {compiled_default}"
                        else:
                            # Fallback for text-based defaults
                            default = f" DEFAULT {col_obj.server_default.arg}"
                    except Exception as e:
                        logger.warning(
                            "Could not compile server_default for column %s.%s: %s. Skipping default.",
                            table_name, col_obj.name, e
                        )
                
                col_definition = f'"{col_obj.name}" {col_type}{nullable}{default}'
                missing.append((col_obj.name, col_definition))
        
        return missing
        
    except Exception as e:
        logger.error("Failed to analyze table %s: %s", table_name, e)
        # Re-raise to fail fast instead of returning empty list
        raise RuntimeError(f"Schema inspection failed for table {table_name}") from e


def ensure_table_schema(db: Session, table_name: str, model_table: Table) -> None:
    """Ensure the database table matches the SQLAlchemy model."""
    try:
        inspector = inspect(db.bind)
        
        # Check if table exists
        if not inspector.has_table(table_name):
            logger.info("Table %s does not exist, will be created by create_all()", table_name)
            return
        
        # Get missing columns
        missing_columns = get_missing_columns(db, table_name, model_table)
        
        if not missing_columns:
            logger.debug("Table %s schema is up to date", table_name)
            return

        formatted_missing = ", ".join(f"{col} -> {definition}" for col, definition in missing_columns)
        log_migration_action(table_name, "detected_missing_columns", formatted_missing)
        
        # Add missing columns
        for col_name, col_definition in missing_columns:
            try:
                # For NOT NULL columns with defaults, use a two-step approach to ensure compatibility
                if " NOT NULL" in col_definition and " DEFAULT " in col_definition:
                    # Extract default value
                    default_part = col_definition.split(" DEFAULT ")[1]
                    col_definition_nullable = col_definition.replace(" NOT NULL", "").replace(f" DEFAULT {default_part}", "")
                    
                    # Step 1: Add column as nullable with default
                    sql_add_nullable = f'ALTER TABLE "{table_name}" ADD COLUMN {col_definition_nullable} DEFAULT {default_part}'
                    log_migration_action(table_name, "add_column_with_default_nullable", sql_add_nullable)
                    db.execute(text(sql_add_nullable))
                    
                    # Step 2: Update any NULL values (shouldn't be any with DEFAULT, but be safe)
                    sql_backfill = f'UPDATE "{table_name}" SET "{col_name}" = {default_part} WHERE "{col_name}" IS NULL'
                    log_migration_action(table_name, "backfill_null_values", sql_backfill)
                    update_result = db.execute(text(sql_backfill))
                    if update_result.rowcount > 0:
                        log_migration_action(
                            table_name,
                            "backfill_null_values_result",
                            f"column={col_name}, rows_updated={update_result.rowcount}, default={default_part}",
                        )
                    
                    # Step 3: Make column NOT NULL
                    sql_set_not_null = f'ALTER TABLE "{table_name}" ALTER COLUMN "{col_name}" SET NOT NULL'
                    log_migration_action(table_name, "set_not_null", sql_set_not_null)
                    db.execute(text(sql_set_not_null))
                else:
                    # For other columns, add directly
                    sql_add_column = f'ALTER TABLE "{table_name}" ADD COLUMN {col_definition}'
                    log_migration_action(table_name, "add_column", sql_add_column)
                    db.execute(text(sql_add_column))
            except Exception as e:
                logger.error("Failed to add column %s to table %s: %s", col_name, table_name, e)
                raise
        
        if missing_columns:
            db.commit()
            log_migration_action(
                table_name,
                "schema_update_committed",
                f"added_columns={len(missing_columns)}",
            )
            
    except Exception as e:
        logger.error("Failed to update schema for table %s: %s", table_name, e)
        db.rollback()
        raise


def prepare_dn_table_migration(db: Session) -> None:
    """
    Prepare DN table for migration by handling old schema.

    Only execute if both status and status_delivery columns exist:
    1. Drop status_delivery column
    2. Rename status column to status_delivery and make it nullable
    """
    logger.info("Preparing DN table for migration")

    try:
        inspector = inspect(db.bind)

        # Check if dn table exists
        if not inspector.has_table("dn"):
            logger.info("DN table does not exist yet, skipping preparation")
            return

        # Get existing columns
        existing_columns = {col['name'].lower(): col for col in inspector.get_columns("dn")}

        # Only proceed if both status and status_delivery exist
        has_status = 'status' in existing_columns
        has_status_delivery = 'status_delivery' in existing_columns

        if not (has_status):
            logger.info(
                "Skipping DN table preparation)"
            )
            return

        logger.info("Both status and status_delivery columns exist, proceeding with migration")

        # Step 1: Drop status_delivery
        if has_status_delivery:
            sql_drop_dn = 'ALTER TABLE "dn" DROP COLUMN "status_delivery"'
            log_migration_action("dn", "drop_column", sql_drop_dn)
            db.execute(text(sql_drop_dn))
            db.commit()
            log_migration_action("dn", "drop_column_committed", 'status_delivery')

        # Step 2: Rename status to status_delivery and make it nullable
        sql_rename_dn = 'ALTER TABLE "dn" RENAME COLUMN "status" TO "status_delivery"'
        sql_make_nullable_dn = 'ALTER TABLE "dn" ALTER COLUMN "status_delivery" DROP NOT NULL'
        log_migration_action("dn", "rename_column", sql_rename_dn)
        db.execute(text(sql_rename_dn))
        log_migration_action("dn", "alter_column", sql_make_nullable_dn)
        db.execute(text(sql_make_nullable_dn))
        db.commit()
        log_migration_action("dn", "rename_and_make_nullable_committed", "status -> status_delivery")

        logger.info("Completed DN table preparation")

        # Also prepare dn_record table using the same logic
        try:
            if not inspector.has_table("dn_record"):
                logger.info("dn_record table does not exist, skipping dn_record preparation")
            else:
                existing_rec_cols = {col['name'].lower(): col for col in inspector.get_columns("dn_record")}
                has_status_rec = 'status' in existing_rec_cols
                has_status_delivery_rec = 'status_delivery' in existing_rec_cols

                if has_status_rec:
                    logger.info("Preparing dn_record table migration: found 'status' column")
                    if has_status_delivery_rec:
                        sql_drop_dn_record = 'ALTER TABLE "dn_record" DROP COLUMN "status_delivery"'
                        log_migration_action("dn_record", "drop_column", sql_drop_dn_record)
                        db.execute(text(sql_drop_dn_record))
                        db.commit()
                        log_migration_action("dn_record", "drop_column_committed", 'status_delivery')

                    sql_rename_dn_record = 'ALTER TABLE "dn_record" RENAME COLUMN "status" TO "status_delivery"'
                    sql_make_nullable_dn_record = 'ALTER TABLE "dn_record" ALTER COLUMN "status_delivery" DROP NOT NULL'
                    log_migration_action("dn_record", "rename_column", sql_rename_dn_record)
                    db.execute(text(sql_rename_dn_record))
                    log_migration_action("dn_record", "alter_column", sql_make_nullable_dn_record)
                    db.execute(text(sql_make_nullable_dn_record))
                    db.commit()
                    log_migration_action(
                        "dn_record",
                        "rename_and_make_nullable_committed",
                        "status -> status_delivery",
                    )
                else:
                    logger.info("No 'status' column in dn_record table, skipping dn_record preparation")
        except Exception as e:
            logger.error("dn_record table preparation failed: %s", e)
            db.rollback()
            raise
    except Exception as e:
        logger.error("DN table preparation failed: %s", e)
        db.rollback()
        raise


def run_startup_migrations(db: Session) -> None:
    """Run all necessary startup migrations to sync database schema with models."""
    logger.info("Running startup database migrations")

    try:
        # Get all tables from the Base metadata
        for table_name, table in Base.metadata.tables.items():
            ensure_table_schema(db, table_name, table)

        logger.info("Completed startup database migrations")

    except Exception as e:
        logger.error("Startup migrations failed: %s", e)
        raise
