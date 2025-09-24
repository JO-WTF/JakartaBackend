from __future__ import annotations

from datetime import datetime
from typing import Any, Dict

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import DN, DNRecord
from app.routers.dn import (
    batch_get_dn_records,
    batch_search_dn_list,
    batch_update_dn,
    create_dn_update,
    delete_dn_record,
    edit_dn_record,
    extend_dn_columns_api,
    get_all_dn_records,
    get_dn_filter_options,
    get_dn_list,
    get_dn_records,
    get_dn_status_delivery_stats,
    remove_dn,
    search_dn_list_api,
    search_dn_records,
)
from app.schemas import DNBatchCreateRequest, DNColumnExtensionRequest, DNEditPayload


def _create_dn(
    db_session: Session,
    *,
    dn_number: str,
    status: str = "ON THE WAY",
    du_id: str | None = None,
    remark: str | None = None,
):
    return create_dn_update(
        dnNumber=dn_number,
        status=status,
        remark=remark,
        duId=du_id,
        photo=None,
        lng=None,
        lat=None,
        db=db_session,
    )


def test_extend_dn_columns_handles_success(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: Dict[str, Any] = {}

    def fake_extend(db, columns):
        captured["columns"] = list(columns)
        return ["extra_field"]

    monkeypatch.setattr("app.routers.dn.extend_dn_table_columns", fake_extend)
    response = extend_dn_columns_api(
        request=DNColumnExtensionRequest(columns=["extra_field"]),
        db=db_session,
    )
    assert response.added_columns == ["extra_field"]
    assert "dn_number" in response.columns
    assert captured["columns"] == ["extra_field"]


def test_extend_dn_columns_rejects_invalid_column(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_extend(db, columns):
        raise ValueError("bad column")

    monkeypatch.setattr("app.routers.dn.extend_dn_table_columns", fake_extend)
    with pytest.raises(HTTPException) as exc:
        extend_dn_columns_api(
            request=DNColumnExtensionRequest(columns=["bad name"]),
            db=db_session,
        )
    assert exc.value.detail == "bad column"


def test_create_dn_update_persists_record(db_session: Session) -> None:
    result = _create_dn(
        db_session,
        dn_number="DN-100",
        du_id="du-123",
        status="ON SITE",
        remark="  hello ",
    )
    assert result.ok is True

    record = db_session.query(DNRecord).one()
    assert record.dn_number == "DN-100"
    assert record.du_id == "DU-123"
    assert record.status == "ON SITE"
    assert record.remark == "hello"

    dn_entity = db_session.query(DN).filter(DN.dn_number == "DN-100").one()
    assert dn_entity.du_id == "DU-123"


def test_batch_update_dn_returns_mixed_result(db_session: Session) -> None:
    payload = DNBatchCreateRequest(dn_numbers=["DN200", "", "DN201", "dn200"])
    result = batch_update_dn(payload=payload, db=db_session)
    assert result.status == "ok"
    assert result.success_count == 2
    assert result.failure_count == 2
    assert sorted(result.success_dn_numbers) == ["DN200", "DN201"]
    assert result.failure_details["<empty>"] == "无效的 DN number"
    assert result.failure_details["DN200"] == "请求中重复"


def test_search_dn_records_filters_by_du(db_session: Session) -> None:
    _create_dn(db_session, dn_number="DN-300", du_id="DU-300", status="ON SITE")
    _create_dn(db_session, dn_number="DN-301", du_id="DU-301", status="POD")

    payload = search_dn_records(
        dn_number=None,
        du_id="du-300",
        status=None,
        remark=None,
        has_photo=None,
        date_from=None,
        date_to=None,
        page=1,
        page_size=20,
        db=db_session,
    )
    assert payload.total == 1
    assert payload.items[0].dn_number == "DN-300"


def test_get_dn_records_returns_all_entries(db_session: Session) -> None:
    _create_dn(db_session, dn_number="DN-400", status="ON SITE")
    _create_dn(db_session, dn_number="DN-400", status="POD")

    payload = get_dn_records(dn_number="DN-400", db=db_session)
    assert len(payload.items) == 2


def test_get_dn_filters_returns_distinct_values(db_session: Session) -> None:
    dn_one = DN(
        dn_number="DN-500",
        status="ON SITE",
        status_wh="READY",
        lsp="LSP-A",
        region="Region-A",
        subcon="Sub-A",
        plan_mos_date="01 Jan 24",
        status_delivery="POD",
    )
    dn_two = DN(
        dn_number="DN-501",
        status="ON THE WAY",
        status_wh="READY",
        lsp="LSP-B",
        region="Region-B",
        subcon="Sub-B",
        plan_mos_date="02 Jan 24",
        status_delivery="ON THE WAY",
    )
    db_session.add_all([dn_one, dn_two])
    db_session.commit()

    payload = get_dn_filter_options(db=db_session)
    data = payload.data
    assert "LSP-A" in data["lsp"]
    assert "Region-B" in data["region"]
    assert "01 Jan 24" in data["plan_mos_date"]
    assert data["total"] == 2


def test_get_dn_status_delivery_stats_computes_totals(db_session: Session) -> None:
    today = datetime.now().strftime("%d %b %y")
    dn_one = DN(
        dn_number="DN-600",
        lsp="Carrier-1",
        plan_mos_date=today,
        status_delivery="ON SITE",
    )
    dn_two = DN(
        dn_number="DN-601",
        lsp="Carrier-1",
        plan_mos_date=today,
        status_delivery=None,
    )
    db_session.add_all([dn_one, dn_two])
    db_session.commit()

    payload = get_dn_status_delivery_stats(
        lsp="Carrier-1", plan_mos_date=today, db=db_session
    )
    assert payload.total == 2
    statuses = {row.status_delivery: row.count for row in payload.data}
    assert statuses["ON SITE"] == 1
    assert statuses["NO STATUS"] == 1


def test_get_all_dn_records_returns_list(db_session: Session) -> None:
    _create_dn(db_session, dn_number="DN-700", status="ON SITE")
    _create_dn(db_session, dn_number="DN-701", status="POD")

    payload = get_all_dn_records(db=db_session)
    assert payload.total == 2
    numbers = {item.dn_number for item in payload.items}
    assert numbers == {"DN-700", "DN-701"}


def test_batch_search_dn_list_returns_latest_record(db_session: Session) -> None:
    _create_dn(db_session, dn_number="DN-800", status="ON SITE")
    _create_dn(db_session, dn_number="DN-800", status="POD", remark="second")

    payload = batch_search_dn_list(
        dn_number=["DN-800"],
        page=1,
        page_size=20,
        db=db_session,
    )
    assert payload.total == 1
    item = payload.items[0]
    assert item.dn_number == "DN-800"
    assert item.latest_record_created_at is not None


def test_edit_dn_record_allows_json_payload(db_session: Session) -> None:
    result = _create_dn(db_session, dn_number="DN-900", status="ON SITE")
    record_id = result.id

    payload = edit_dn_record(
        record_id,
        status=None,
        remark=None,
        duId=None,
        photo=None,
        json_body=DNEditPayload(status="POD", remark="done", du_id="DU-500"),
        db=db_session,
    )
    assert payload.id == record_id

    record = db_session.get(DNRecord, record_id)
    assert record is not None
    assert record.status == "POD"
    assert record.remark == "done"
    assert record.du_id == "DU-500"


def test_delete_dn_record_removes_entry(db_session: Session) -> None:
    result = _create_dn(db_session, dn_number="DN-901", status="ON SITE")
    record_id = result.id

    response = delete_dn_record(record_id, db=db_session)
    assert response.ok is True

    remaining = db_session.query(DNRecord).filter(DNRecord.id == record_id).all()
    assert not remaining


def test_delete_dn_endpoint_removes_dn(db_session: Session) -> None:
    _create_dn(db_session, dn_number="DN-902", status="ON SITE")

    response = remove_dn(dn_number="DN-902", db=db_session)
    assert response.ok is True

    assert db_session.query(DN).count() == 0
    assert db_session.query(DNRecord).count() == 0


def test_get_dn_list_returns_rows(db_session: Session) -> None:
    _create_dn(db_session, dn_number="DN-903", status="ON SITE")
    _create_dn(db_session, dn_number="DN-904", status="POD")

    payload = get_dn_list(db=db_session)
    numbers = {item.dn_number for item in payload.data}
    assert numbers == {"DN-903", "DN-904"}


def test_search_dn_list_api_handles_filters(db_session: Session) -> None:
    dn_with_coords = DN(
        dn_number="DN-905",
        du_id="DU-905",
        status="ON SITE",
        status_delivery="ON THE WAY",
        lsp="LSP-X",
        region="Region-X",
        area="Area-X",
        status_wh="READY",
        subcon="Sub-X",
        project_request="Project-X",
        lng="106.8",
        lat="-6.2",
        plan_mos_date="01 Jan 24",
    )
    dn_without_match = DN(
        dn_number="DN-906",
        status="",
        status_delivery="",
        lsp="LSP-Y",
        region="Region-Y",
        area="Area-Y",
        status_wh="PENDING",
        subcon="Sub-Y",
        project_request="Project-Y",
        plan_mos_date="02 Jan 24",
    )
    db_session.add_all([dn_with_coords, dn_without_match])
    db_session.add(DNRecord(dn_number="DN-905", status="ON SITE", remark="first"))
    db_session.commit()

    payload = search_dn_list_api(
        date=["01 Jan 24"],
        dn_number=None,
        du_id=None,
        status_delivery=["ON THE WAY"],
        status_values_param=["ON SITE"],
        status_not_empty=True,
        has_coordinate=True,
        lsp=["LSP-X"],
        region=["Region-X"],
        area="Area-X",
        status_wh=["READY"],
        subcon=["Sub-X"],
        project="Project-X",
        page=1,
        page_size=20,
        db=db_session,
    )
    assert payload.total == 1
    assert payload.items[0].dn_number == "DN-905"


def test_batch_get_dn_records_returns_paginated(db_session: Session) -> None:
    _create_dn(db_session, dn_number="DN-907", status="ON SITE")
    _create_dn(db_session, dn_number="DN-908", status="POD")

    payload = batch_get_dn_records(
        dn_number=["DN-907", "DN-908"],
        page=1,
        page_size=20,
        db=db_session,
    )
    assert payload.total == 2
    numbers = {item.dn_number for item in payload.items}
    assert numbers == {"DN-907", "DN-908"}
