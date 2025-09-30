# Jakarta Backend

A FastAPI-based backend service for DN (Delivery Note) management with Google Sheets integration.

## Features

- DN (Delivery Note) CRUD operations with soft delete support
- Google Sheets bidirectional synchronization
- Vehicle management and tracking
- Status delivery analytics and reporting
- Scheduled data synchronization
- File upload and storage (S3/local)
- Database migrations with automatic schema updates

## Technology Stack

- **Framework**: FastAPI 0.116.2
- **Database**: PostgreSQL with SQLAlchemy 2.0.36 ORM
- **Authentication**: Vehicle-based authentication system
- **Task Scheduling**: APScheduler for background jobs
- **Data Processing**: Pandas for Google Sheets data manipulation
- **Cloud Storage**: AWS S3 via boto3
- **Validation**: Pydantic for data validation

## Setup

### Prerequisites

- Python 3.11+ (tested with 3.13.3)
- PostgreSQL database
- Google Sheets API credentials

### Installation

#### Quick Setup (Recommended)

Run the automated setup script:
```bash
chmod +x setup.sh
./setup.sh
```

The script will:
- Check your Python version and warn about compatibility issues
- Install PostgreSQL tools if needed (macOS)
- Let you choose between production, development, or Python 3.13 compatible dependencies
- Handle virtual environment detection

#### Manual Setup

1. **Create a virtual environment** (recommended):
   ```bash
   python3 -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

2. **Install PostgreSQL tools** (macOS only):
   ```bash
   brew install postgresql@14
   export PATH="/opt/homebrew/opt/postgresql@14/bin:$PATH"
   ```

3. **Install dependencies**:
   ```bash
   # Production environment
   pip install -r requirements.txt
   
   # Development environment (includes testing and code quality tools)
   pip install -r requirements-dev.txt
   
   # Python 3.13 compatibility (if needed)
   pip install -r requirements-python313.txt
   ```

4. **Set up environment variables** (see Configuration section)

5. **Run database migrations** (automatic on startup)

6. **Start the server**:
   ```bash
   uvicorn app.main:app --reload
   ```

## Configuration

Create a `.env` file or set environment variables:

```env
DATABASE_URL=postgresql://user:password@localhost/dbname
GOOGLE_SHEETS_CREDENTIALS_JSON=path/to/credentials.json
SPREADSHEET_URL=https://docs.google.com/spreadsheets/d/your-sheet-id
AWS_ACCESS_KEY_ID=your-aws-key
AWS_SECRET_ACCESS_KEY=your-aws-secret
S3_BUCKET_NAME=your-bucket-name
```

## API Documentation

Once running, access the interactive API documentation at:
- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

## Key Features

### Soft Delete System
- All DN records use `is_deleted` field for soft deletion
- Deleted records are hidden from normal queries but preserved for audit

### Google Sheets Sync
- Automatic bidirectional sync every 5 minutes
- Missing DNs in sheets are marked as deleted
- Present DNs are automatically restored

### Database Migrations
- Automatic schema updates on startup
- Detects missing columns and adds them safely
- Handles complex default values and constraints

### Scheduled Tasks
- DN sheet synchronization: every 5 minutes
- Status delivery statistics: hourly aggregation

## Development

### Running Tests

```bash
pytest
```

### Code Quality

```bash
# Format code
black .

# Sort imports
isort .

# Lint code
flake8 .

# Type checking
mypy .
```

## Project Structure

```
app/
├── api/           # API endpoints
├── core/          # Core business logic
├── schemas/       # Pydantic models
├── utils/         # Utility functions
├── models.py      # SQLAlchemy models
├── crud.py        # Database operations
├── db.py          # Database configuration
└── main.py        # Application entry point
```

## Dependencies

See `requirements.txt` for production dependencies and `requirements-dev.txt` for development tools.

## Troubleshooting

### psycopg2-binary Installation Issues

If you encounter issues installing `psycopg2-binary` on macOS:

1. **Install PostgreSQL via Homebrew** (provides pg_config):
   ```bash
   brew install postgresql
   ```

2. **Alternative: Use psycopg3** (modern replacement):
   ```bash
   pip install psycopg[binary]
   ```

3. **For Apple Silicon Macs**, you might need:
   ```bash
   brew install libpq
   export LDFLAGS="-L$(brew --prefix libpq)/lib"
   export CPPFLAGS="-I$(brew --prefix libpq)/include"
   pip install psycopg2-binary
   ```

### Virtual Environment Issues

If pip complains about externally managed environment:

```bash
# Create and use a virtual environment
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Or use --break-system-packages (not recommended for production)
pip install -r requirements.txt --break-system-packages
```

### Python Version Compatibility

- **Recommended**: Python 3.11 or 3.12
- **Python 3.13**: Some packages may have compatibility issues
- If using Python 3.13, consider using Python 3.12 instead:
  ```bash
  pyenv install 3.12.7
  pyenv local 3.12.7
  ```
