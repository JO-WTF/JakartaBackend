"""Database schema migration utilities."""

from sqlalchemy import text, inspect, Table
from sqlalchemy.orm import Session
from app.utils.logging import logger
from app.models import Base


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
        
        # Add missing columns
        for col_name, col_definition in missing_columns:
            logger.info("Adding column %s to table %s", col_name, table_name)
            try:
                # For NOT NULL columns with defaults, use a two-step approach to ensure compatibility
                if " NOT NULL" in col_definition and " DEFAULT " in col_definition:
                    # Extract default value
                    default_part = col_definition.split(" DEFAULT ")[1]
                    col_definition_nullable = col_definition.replace(" NOT NULL", "").replace(f" DEFAULT {default_part}", "")
                    
                    # Step 1: Add column as nullable with default
                    db.execute(text(f'ALTER TABLE "{table_name}" ADD COLUMN {col_definition_nullable} DEFAULT {default_part}'))
                    logger.info("Added nullable column %s with default value", col_name)
                    
                    # Step 2: Update any NULL values (shouldn't be any with DEFAULT, but be safe)
                    update_result = db.execute(text(
                        f'UPDATE "{table_name}" SET "{col_name}" = {default_part} WHERE "{col_name}" IS NULL'
                    ))
                    if update_result.rowcount > 0:
                        logger.info("Updated %d existing rows with default value for %s", update_result.rowcount, col_name)
                    
                    # Step 3: Make column NOT NULL
                    db.execute(text(f'ALTER TABLE "{table_name}" ALTER COLUMN "{col_name}" SET NOT NULL'))
                    logger.info("Set column %s to NOT NULL", col_name)
                else:
                    # For other columns, add directly
                    db.execute(text(f'ALTER TABLE "{table_name}" ADD COLUMN {col_definition}'))
                
                logger.info("Successfully added column %s to table %s", col_name, table_name)
            except Exception as e:
                logger.error("Failed to add column %s to table %s: %s", col_name, table_name, e)
                raise
        
        if missing_columns:
            db.commit()
            logger.info("Updated schema for table %s: added %d columns", table_name, len(missing_columns))
            
    except Exception as e:
        logger.error("Failed to update schema for table %s: %s", table_name, e)
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
