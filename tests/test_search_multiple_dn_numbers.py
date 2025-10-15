"""Test DN list search with multiple DN numbers."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base, get_db
from app.main import app
from app.models import DN


@pytest.fixture
def db_session():
    """Create a test database session with in-memory SQLite."""
    # Use in-memory SQLite database for testing
    # connect_args={"check_same_thread": False} allows SQLite to be used across threads
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        future=True
    )
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(
        bind=engine, autoflush=False, autocommit=False, future=True
    )
    
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db_session):
    """Create test client with test database."""
    def override_get_db():
        try:
            yield db_session
        finally:
            pass
    
    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def sample_dns(db_session):
    """Create sample DN records for testing."""
    # Create test DNs
    dns = [
        DN(
            dn_number="JKT001-20241007",
            status="Delivered",
            plan_mos_date="2024-10-07",
            lsp="LSP-A",
            region="Jakarta",
        ),
        DN(
            dn_number="JKT002-20241007",
            status="In Transit",
            plan_mos_date="2024-10-07",
            lsp="LSP-A",
            region="Jakarta",
        ),
        DN(
            dn_number="JKT003-20241007",
            status="Pending",
            plan_mos_date="2024-10-07",
            lsp="LSP-B",
            region="Jakarta",
        ),
        DN(
            dn_number="JKT004-20241007",
            status="Delivered",
            plan_mos_date="2024-10-08",
            lsp="LSP-B",
            region="Surabaya",
        ),
    ]
    db_session.add_all(dns)
    db_session.commit()
    
    for dn in dns:
        db_session.refresh(dn)
    
    return dns


def test_search_single_dn_number(client, sample_dns):
    """Test searching with a single DN number."""
    response = client.get("/api/dn/list/search?dn_number=JKT001-20241007")
    assert response.status_code == 200
    
    data = response.json()
    assert data["ok"] is True
    assert data["total"] == 1
    assert len(data["items"]) == 1
    assert data["items"][0]["dn_number"] == "JKT001-20241007"


def test_search_multiple_dn_numbers_repeated_params(client, sample_dns):
    """Test searching with multiple DN numbers using repeated parameters."""
    response = client.get("/api/dn/list/search?dn_number=JKT001-20241007&dn_number=JKT003-20241007")
    assert response.status_code == 200
    
    data = response.json()
    assert data["ok"] is True
    assert data["total"] == 2
    assert len(data["items"]) == 2
    
    dn_numbers = {item["dn_number"] for item in data["items"]}
    assert dn_numbers == {"JKT001-20241007", "JKT003-20241007"}


def test_search_multiple_dn_numbers_comma_separated(client, sample_dns):
    """Test searching with multiple DN numbers using comma-separated values."""
    response = client.get("/api/dn/list/search?dn_number=JKT001-20241007,JKT002-20241007")
    assert response.status_code == 200
    
    data = response.json()
    assert data["ok"] is True
    assert data["total"] == 2
    assert len(data["items"]) == 2
    
    dn_numbers = {item["dn_number"] for item in data["items"]}
    assert dn_numbers == {"JKT001-20241007", "JKT002-20241007"}


def test_search_multiple_dn_numbers_mixed(client, sample_dns):
    """Test searching with multiple DN numbers using mixed formats."""
    response = client.get(
        "/api/dn/list/search?dn_number=JKT001-20241007,JKT002-20241007&dn_number=JKT004-20241007"
    )
    assert response.status_code == 200
    
    data = response.json()
    assert data["ok"] is True
    assert data["total"] == 3
    assert len(data["items"]) == 3
    
    dn_numbers = {item["dn_number"] for item in data["items"]}
    assert dn_numbers == {"JKT001-20241007", "JKT002-20241007", "JKT004-20241007"}


def test_search_multiple_dn_numbers_with_other_filters(client, sample_dns):
    """Test searching with multiple DN numbers combined with other filters."""
    # Search for specific DNs within LSP-A
    response = client.get(
        "/api/dn/list/search?dn_number=JKT001-20241007&dn_number=JKT003-20241007&lsp=LSP-A"
    )
    assert response.status_code == 200
    
    data = response.json()
    assert data["ok"] is True
    # Only JKT001 should match (JKT003 is LSP-B)
    assert data["total"] == 1
    assert data["items"][0]["dn_number"] == "JKT001-20241007"


def test_search_dn_numbers_with_whitespace(client, sample_dns):
    """Test searching with DN numbers that have whitespace."""
    response = client.get("/api/dn/list/search?dn_number= JKT001-20241007 , JKT002-20241007 ")
    assert response.status_code == 200
    
    data = response.json()
    assert data["ok"] is True
    assert data["total"] == 2
    
    dn_numbers = {item["dn_number"] for item in data["items"]}
    assert dn_numbers == {"JKT001-20241007", "JKT002-20241007"}


def test_search_nonexistent_dn_numbers(client, sample_dns):
    """Test searching with DN numbers that don't exist."""
    response = client.get("/api/dn/list/search?dn_number=JKT999-20241007")
    assert response.status_code == 200
    
    data = response.json()
    assert data["ok"] is True
    assert data["total"] == 0
    assert len(data["items"]) == 0


def test_search_dn_numbers_duplicate_values(client, sample_dns):
    """Test searching with duplicate DN numbers - should be deduplicated."""
    response = client.get(
        "/api/dn/list/search?dn_number=JKT001-20241007&dn_number=JKT001-20241007"
    )
    assert response.status_code == 200
    
    data = response.json()
    assert data["ok"] is True
    assert data["total"] == 1
    assert len(data["items"]) == 1
    assert data["items"][0]["dn_number"] == "JKT001-20241007"


def test_search_dn_numbers_with_pagination(client, sample_dns):
    """Test searching with multiple DN numbers and pagination."""
    response = client.get(
        "/api/dn/list/search?dn_number=JKT001-20241007&dn_number=JKT002-20241007&dn_number=JKT003-20241007&page=1&page_size=2"
    )
    assert response.status_code == 200
    
    data = response.json()
    assert data["ok"] is True
    assert data["total"] == 3
    assert len(data["items"]) == 2  # Only 2 items per page


def test_search_without_dn_numbers(client, sample_dns):
    """Test searching without DN number filter - should return all records."""
    response = client.get("/api/dn/list/search")
    assert response.status_code == 200
    
    data = response.json()
    assert data["ok"] is True
    assert data["total"] == 4  # All 4 sample DNs
    assert len(data["items"]) == 4
