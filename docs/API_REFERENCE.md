# JakartaBackend API Reference

This document describes every public endpoint exposed by the JakartaBackend FastAPI application.  
All responses are JSON unless explicitly stated otherwise.

---

## Overview

- **Base URL:** `https://your-domain.example` (replace with the deployed host)
- **API version:** `1.1.0` (as declared in `app.main`)
- **Authentication:** Not enforced by the backend. Deploy behind your own gateway if authentication is required.

---

## Conventions

### Response Envelope

Successful handlers return an object with `ok: true` and endpoint-specific fields. Errors raised by the application use HTTP status codes with either:

```json
{"ok": false, "error": "error_code", "detail": "human readable message"}
```

or the default FastAPI structure:

```json
{"detail": "Validation error or exception message"}
```

### Time Zone & Formatting

- Internal timestamps are stored in UTC.
- API responses are converted to Jakarta time (GMT+7) using ISO-8601 strings, e.g. `"2025-01-12T14:30:00+07:00"`.
- Query parameters expecting a date use the format `YYYY-MM-DD` unless otherwise noted.

### Identifier normalization

- Delivery Note numbers are normalized (uppercase and trimmed) internally. Passing an invalid DN number yields `400`.
- Status delivery values are normalized to canonical labels such as `On the way`, `On Site`, `POD`, etc. Blank or unknown values become `No Status`.
- Vehicle plates are normalized by stripping whitespace and uppercasing before persistence.

### Mixed naming conventions

- Request bodies follow the aliases defined in Pydantic schemas (for example `vehiclePlate`, `LSP`).
- Response payloads may mix snake_case (database columns) and camelCase (vehicle responses) depending on the originating service.

---

## Health

#### GET /
- **Description:** Lightweight probe used for uptime checks.
- **Query parameters:** None.
- **Response example (200):**
```json
{
  "ok": true,
  "message": "You can use admin panel now."
}
```

---

## Delivery Note APIs (`/api/dn`)

### Column management

#### POST /api/dn/columns/extend
- **Description:** Adds text columns to the `dn` table (and Google Sheet mapping) if they do not already exist.
- **Request body (application/json):**
```json
{
  "columns": ["custom_note", "last_mile_eta"]
}
```
- **Responses:**
  - `200` with the list of newly created columns plus the up-to-date sheet column list.
```json
{
  "ok": true,
  "added_columns": ["custom_note"],
  "columns": [
    "dn_number",
    "du_id",
    "... trimmed ...",
    "custom_note"
  ]
}
```
  - `400` when the request contains an invalid column name.

---

### Delivery note updates & history

#### POST /api/dn/update
- **Description:** Create a DN history record, update the master DN row, upload an optional photo, and sync status information to Google Sheet.
- **Content type:** `multipart/form-data`
- **Form fields:**
  - `dnNumber` *(string, required)* – DN identifier.
  - `status` *(string, optional)* – legacy status field; used as `status_delivery` fallback.
  - `status_delivery` *(string, optional)* – delivery status; triggers timestamp sync for selected statuses.
  - `status_site` *(string, optional)* – site status.
  - `remark` *(string, optional)*.
  - `photo` *(file, optional)* – uploaded image; stored via `app.storage.save_file`.
  - `lng`, `lat` *(string/float, optional)* – coordinates.
  - `updated_by` *(string, optional)* – operator/driver name. When submitted by an authenticated admin view caller, append `"(by username)"` to indicate the source.
  - `phone_number` *(string, optional)* – driver contact.
  - `duId`, `du_id` are ignored (legacy clients should use the fields above).
- **Behavior notes:**
  - Status strings are normalized. When the resulting value is in `ARRIVAL_STATUSES` or `DEPARTURE_STATUSES`, the corresponding ATA/ATD timestamp is written in both DB and Google Sheets.
  - `updated_by` is trimmed; blank strings become `null`.
  - Admin portal submissions should format `updated_by` as `"Driver Name (by alice)"`.
  - When the DN has a known Google Sheet row, columns `status_delivery`, `status_site`, `issue_remark`, `driver_contact_name` (J column), and `driver_contact_number` (K column) are updated in Sheets with audit notes.
- **Sample cURL request:**
```bash
curl -X POST "$BASE_URL/api/dn/update" \
  -F 'dnNumber=DN001-20250101' \
  -F 'status_delivery=On the way' \
  -F 'status_site=Arrived' \
  -F 'remark=Unloaded at gate' \
  -F 'updated_by=Rudi (by alice)' \
  -F 'phone_number=081234567890'
```
- **Response example (200):**
```json
{
  "ok": true,
  "id": 7051,
  "photo": null,
  "delivery_status_update_result": {
    "updated": true,
    "sheet": "Plan MOS Jan",
    "row": 42,
    "status_delivery_updated": true,
    "status_site_updated": true,
    "issue_remark_updated": true,
    "driver_contact_name_updated": true,
    "driver_contact_number_updated": true
  }
}
```

#### POST /api/dn/batch_update
- **Description:** Create multiple DN master rows with default status history records.
- **Request body (application/json):**
```json
{
  "dn_numbers": [
    "DN001-20250101",
    "DN002-20250101"
  ]
}
```
- **Response example (200):**
```json
{
  "status": "ok",
  "success_count": 2,
  "failure_count": 0,
  "success_dn_numbers": ["DN001-20250101", "DN002-20250101"],
  "failure_details": {}
}
```
- **Failure cases:**
  - Empty list -> `status: "fail", errmessage: "DN number 列表为空"`.
  - Invalid DN format or duplicates populate `failure_details`.

#### DELETE /api/dn/update/{id}
- **Description:** Delete a single DN history record (table `dn_record`).
- **Path parameters:**
  - `id` *(integer, required)* – record identifier.
- **Responses:**
  - `200` when the record existed:
```json
{"ok": true}
```
  - `404` when the record is missing.

#### DELETE /api/dn/{dn_number}
- **Description:** Permanently remove a DN master row and all associated history records.
- **Path parameters:**
  - `dn_number` *(string, required)* – normalized DN identifier.
- **Responses:**
  - `200` with a summary of deleted data.
```json
{
  "ok": true
}
```
  - `404` if the DN does not exist.

#### GET /api/dn/{dn_number}
- **Description:** Fetch every history record (`dn_record`) for the given DN.
- **Path parameters:** same as DELETE variant.
- **Response example (200):**
```json
{
  "ok": true,
  "items": [
    {
      "id": 7051,
      "dn_number": "DN001-20250101",
      "status_delivery": "On the way",
      "status_site": "Arrived",
      "remark": "Unloaded at gate",
      "photo_url": null,
      "lng": "106.8456",
      "lat": "-6.2088",
      "updated_by": "Rudi (by alice)",
      "phone_number": "081234567890",
      "created_at": "2025-01-12T08:12:34+07:00"
    }
  ]
}
```

---

### Sheet synchronization

#### POST /api/dn/sync
- **Description:** Manually trigger Google Sheet → database synchronization.
- **Response example (200):**
```json
{
  "ok": true,
  "synced_count": 41,
  "created_count": 8,
  "updated_count": 29,
  "ignored_count": 4,
  "dn_numbers": ["DN001-20250101", "..."]
}
```
- **Errors:** `500` if the sync job raises an exception (response includes `errorInfo` traceback).

#### GET /api/dn/sync/log/latest
- **Description:** Return the newest entry recorded in `dn_sync_log`.
- **Response example (200):**
```json
{
  "ok": true,
  "data": {
    "id": 175,
    "status": "completed",
    "synced_count": 41,
    "dn_numbers": ["DN001-20250101"],
    "message": "Manual sync finished",
    "error_message": null,
    "error_traceback": null,
    "created_at": "2025-01-12T08:20:10+07:00"
  }
}
```

#### GET /api/dn/sync/log/file
- **Description:** Download the raw sync log file generated under `DN_SYNC_LOG_PATH`.
- **Responses:**
  - `200` → `text/plain` file download with `Content-Disposition: attachment`.
  - `404` when the log file has never been written.

---

### Archive tooling

#### POST /api/dn/archive
- **Description:** Archive Plan MOS rows older than two days with delivery status `POD`, moving them to an `Archived YYYY-MM` worksheet while leaving active rows in place.
- **Response example (200):**
```json
{
  "threshold_date": "2025-01-10",
  "processed": [
    {"sheet": "Plan MOS Jan", "kept": 120, "archived": 18},
    {"sheet": "Plan MOS Feb", "kept": 42, "archived": 0}
  ]
}
```
- **Errors:** `200` with `"error": "...message..."` when the Google API interaction fails.

---

### Delivery note listings

#### GET /api/dn/list
- **Description:** Return every active DN sorted by DN number.
- **Query parameters:** None.
- **Response example (200):**
```json
{
  "ok": true,
  "data": [
    {
      "id": 512,
      "dn_number": "DN001-20250101",
      "created_at": "2025-01-02T09:00:00+07:00",
      "status_delivery": "On the way",
      "status_site": "Arrived",
      "remark": "Waiting unloading",
      "photo_url": null,
      "lng": "106.8456",
      "lat": "-6.2088",
      "last_updated_by": "Rudi",
      "gs_sheet": "Plan MOS Jan",
      "gs_row": 42,
      "gs_cell_url": "https://docs.google.com/...#gid=123&range=A42",
      "is_deleted": "N",
      "update_count": 3,
      "driver_contact_number": "081234567890",
      "plan_mos_date": "12 Jan 25",
      "... dynamic columns ..."
    }
  ]
}
```
- **Notes:** every sheet column (static + dynamic) is present on each row.

#### GET /api/dn/list/search
- **Description:** Multi-filter search with optional aggregation statistics.
- **Query parameters:**

| Name | Type | Description |
|------|------|-------------|
| `date` | list[str] | One or more Plan MOS dates (exact string matches, e.g. `"12 Jan 25"`). |
| `dn_number` | list[str] | DN numbers (repeated or comma-separated). |
| `du_id` | string | Filter by DU id (exact match). |
| `phone_number` | string | Driver phone (whitespace trimmed). |
| `status_delivery` | list[str] | Delivery statuses (case-insensitive). |
| `status_site` | list[str] | Site statuses. |
| `status_delivery_not_empty` | bool | `true` -> require non-empty delivery status. |
| `status_site_not_empty` | bool | `true` -> require non-empty site status. |
| `has_coordinate` | bool | `true` -> both lat & lng present; `false` -> missing coordinates. |
| `show_deleted` | bool | Include soft-deleted rows when `true`. |
| `lsp`, `region`, `area`, `status_wh`, `subcon`, `project_request`, `mos_type` | list[str] | Case-insensitive filters. |
| `date_from`, `date_to` | datetime (ISO-8601) | Filter by last modification window (converted to GMT+7). |
| `page` | int | Page number (ignored when `page_size=all`). |
| `page_size` | int or `"all"` | Items per page (1-2000) or `"all"` to return the entire result set. |

- **Response example (200):**
```json
{
  "ok": true,
  "total": 45,
  "page": 1,
  "page_size": 20,
  "items": [
    {
      "dn_number": "DN001-20250101",
      "status_delivery": "On the way",
      "status_site": "Arrived",
      "plan_mos_date": "12 Jan 25",
      "latest_record_created_at": "2025-01-12T08:12:34+07:00",
      "... other columns ..."
    }
  ],
  "stats": {
    "status_delivery": {
      "On the way": 18,
      "On Site": 12,
      "POD": 15,
      "Total": 45
    },
    "status_site": {
      "Arrived": 27
    },
    "lsp_summary": [
      {"lsp": "LSP A", "total_dn": 20, "status_not_empty": 17},
      {"lsp": "LSP B", "total_dn": 25, "status_not_empty": 22}
    ]
  }
}
```

#### GET /api/dn/list/batch
- **Description:** Fetch DN master rows for explicit DN numbers with pagination.
- **Query parameters:**
  - `dn_number` *(list[str], required)* – repeated or comma-separated.
  - `page` *(int, default 1)*
  - `page_size` *(int, default 20, max 100)*
- **Response:** identical structure to `/list` but limited to the requested numbers.

#### GET /api/dn/list/batch-by-du
- **Description:** Paginated lookup by DU identifiers.
- **Query parameters:**
  - `du_id` *(list[str], required)*
  - `page`, `page_size` – same semantics as above.
- **Response:** same payload shape as `/list`.

#### GET /api/dn/list/early-bird
- **Description:** List DNs that arrived before the configured cutoff (the “early bird” report).
- **Query parameters:**
  - `start_date`, `end_date` *(date, required)* – inclusive range (`YYYY-MM-DD`).
  - `region`, `area`, `lsp` *(list[str], optional)* – filters (case-insensitive).
- **Response example (200):**
```json
{
  "ok": true,
  "total": 3,
  "start_date": "2025-01-01",
  "end_date": "2025-01-07",
  "data": [
    {
      "dn_id": 512,
      "dn_number": "DN001-20250101",
      "plan_mos_date": "12 Jan 25",
      "plan_mos_date_iso": "2025-01-12",
      "lsp": "LSP A",
      "arrival_status": "ARRIVED AT SITE",
      "arrived_at_site_time": "2025-01-12T07:55:00+07:00",
      "cutoff_time": "2025-01-12T08:00:00+07:00",
      "record_updated_by": "Rudi",
      "record_phone_number": "081234567890"
    }
  ]
}
```

#### GET /api/dn/records
- **Description:** Return every `dn_record` in reverse chronological order.
- **Response example (200):**
```json
{
  "ok": true,
  "total": 1205,
  "items": [
    {
      "id": 7051,
      "dn_number": "DN001-20250101",
      "status_delivery": "On the way",
      "remark": "Unloaded at gate",
      "photo_url": null,
      "lng": "106.8456",
      "lat": "-6.2088",
      "updated_by": "Rudi",
      "created_at": "2025-01-12T08:12:34+07:00"
    }
  ]
}
```

---

### Delivery note record search

#### GET /api/dn/search
- **Description:** Search delivery note history records with optional pagination.
- **Query parameters:**
  - `dn_number` *(string)* – exact DN (normalized); invalid format → `400`.
  - `status_delivery`, `status_site` *(string)* – filter by status.
  - `remark` *(string)* – substring match.
  - `phone_number` *(string)* – trimmed before filtering.
  - `has_photo` *(bool)* – require non-null photo URL.
  - `date_from`, `date_to` *(datetime)* – range filter (ISO-8601).
  - `page` *(int, default 1)*
  - `page_size` *(int, optional)* – default returns all matches.
- **Response example (200):**
```json
{
  "ok": true,
  "total": 8,
  "page": 1,
  "page_size": 8,
  "items": [
    {
      "dn_number": "DN001-20250101",
      "status_delivery": "On the way",
      "status_site": "Arrived",
      "remark": "Unloaded at gate",
      "updated_by": "Rudi",
      "phone_number": "081234567890",
      "created_at": "2025-01-12T08:12:34+07:00"
    }
  ]
}
```

#### GET /api/dn/batch
- **Description:** Paginated history lookup for multiple DN numbers.
- **Query parameters:**
  - `dn_number` *(list[str], optional)* – repeated or comma-separated.
  - `dnnumber` *(list[str], optional)* – legacy alias.
  - `page`, `page_size` *(int, defaults 1 & 20)*
- **Response:** same structure as `/api/dn/search`.

---

### Export endpoints

#### GET /api/dn/export/details
- **Description:** Return the master DN row and every historical record for each requested DN.
- **Query parameters:**
  - `dn_number` *(list[str], required)* – repeated or comma-separated.
- **Response example (200):**
```json
{
  "ok": true,
  "count": 2,
  "data": [
    {
      "dn_number": "DN001-20250101",
      "dn": {
        "id": 512,
        "dn_number": "DN001-20250101",
        "plan_mos_date": "12 Jan 25",
        "driver_contact_number": "081234567890",
        "created_at": "2025-01-02T02:00:00+07:00",
        "...": "..."
      },
      "records": [
        {
          "id": 7051,
          "status_delivery": "On the way",
          "remark": "Unloaded at gate",
          "updated_by": "Rudi",
          "created_at": "2025-01-12T08:12:34+07:00"
        }
      ]
    }
  ],
  "not_found_dn_numbers": ["DN003-20250101"]
}
```

#### GET /api/dn/export/details-pdf
- **Description:** Generate a PDF summarizing DN master data and history records (requires `MAPBOX_ACCESS_TOKEN` for map snapshots).
- **Query parameters:** identical to the JSON variant.
- **Responses:**
  - `200` – `application/pdf` with `Content-Disposition: attachment; filename="dn-details-YYYYMMDDHHMMSS.pdf"`.
  - `404` – when none of the requested DNs have data.
  - `500` – when Mapbox credentials are missing.
- **Headers:** `X-Not-Found-DN` lists missing numbers (URL encoded) when partial results exist.

#### GET /api/dn/early-bird/export
- **Description:** Export the Early Bird report (same filters as `/list/early-bird`) to PDF.
- **Query parameters:** `start_date`, `end_date`, `region`, `area`, `lsp`.
- **Responses:** identical semantics to `/export/details-pdf`.

---

### Statistics & filters

#### GET /api/dn/stats/{date}
- **Description:** Count DNs by status delivery for a specific Plan MOS date string (e.g. `"12 Jan 25"`).
- **Path parameter:** `date` *(string, required)* – raw Plan MOS text.
- **Response example (200):**
```json
{
  "ok": true,
  "data": [
    {
      "group": "Total",
      "date": "12 Jan 25",
      "values": [18, 12, 15, 45]
    }
  ]
}
```
- **Notes:** `values` aligns with the canonical status ordering plus overall total.

#### GET /api/dn/filters
- **Description:** Fetch cached distinct values for filters (LSPs, Regions, Areas, etc.).
- **Response example (200):**
```json
{
  "ok": true,
  "data": {
    "dn_numbers": ["DN001-20250101"],
    "lsp": ["LSP A", "LSP B"],
    "region": ["Jabodetabek"],
    "total": 532
  }
}
```

#### GET /api/dn/status-delivery/lsp-summary-records
- **Description:** Retrieve historical snapshots of LSP delivery status coverage plus hourly update aggregates.
- **Query parameters:**
  - `lsp` *(string, optional)* – exact match filter.
  - `limit` *(int, default 5000, max 10000)* – maximum snapshot rows.
- **Response example (200):**
```json
{
  "ok": true,
  "data": {
    "by_plan_mos_date": [
      {
        "id": 12,
        "lsp": "LSP A",
        "total_dn": 45,
        "status_not_empty": 39,
        "plan_mos_date": "12 Jan 25",
        "recorded_at": "2025-01-12T20:00:00+07:00"
      }
    ],
    "by_update_date": [
      {
        "id": 1,
        "lsp": "LSP A",
        "updated_dn": 5,
        "update_date": "12 Jan 25",
        "recorded_at": "2025-01-12 08:00:00"
      }
    ]
  }
}
```

#### GET /api/dn/status_delivery/by-driver
- **Description:** Summarize driver performance by phone number using unique DN and `(DN, status)` counts.
- **Query parameters:**
  - `phone_number` *(string, optional)* – when present, returns stats for a single driver.
- **Response example (200):**
```json
{
  "ok": true,
  "data": [
    {
      "phone_number": "081234567890",
      "unique_dn_count": 12,
      "record_count": 18
    },
    {
      "phone_number": "089876543210",
      "unique_dn_count": 7,
      "record_count": 7
    }
  ],
  "total_drivers": 2
}
```

---

## Vehicle APIs (`/api/vehicle`)

All vehicle endpoints normalize license plates (uppercased, whitespace removed) before validation.

#### POST /api/vehicle/signin
- **Description:** Register or update a vehicle’s arrival at the site.
- **Request body (application/json):**
```json
{
  "vehiclePlate": "B 1234 XYZ",
  "LSP": "LSP A",
  "vehicleType": "CDE Truck",
  "driverName": "Andi",
  "contactNumber": "0812121212",
  "arriveTime": "2025-01-12T07:45:00+07:00"
}
```
- **Response example (200):**
```json
{
  "ok": true,
  "vehicle": {
    "vehiclePlate": "B1234XYZ",
    "vehicleType": "CDE Truck",
    "driverName": "Andi",
    "contactNumber": "0812121212",
    "LSP": "LSP A",
    "status": "arrived",
    "arriveTime": "2025-01-12T07:45:00+07:00",
    "departTime": null,
    "createdAt": "2025-01-12T07:45:00+07:00",
    "updatedAt": "2025-01-12T07:45:00+07:00"
  }
}
```
- **Validation failures:** `400` with `vehicle_plate_required` or `lsp_required`.

#### POST /api/vehicle/depart
- **Description:** Mark a vehicle as departed and capture an optional timestamp.
- **Request body (application/json):**
```json
{
  "vehiclePlate": "B 1234 XYZ",
  "departTime": "2025-01-12T09:15:00+07:00"
}
```
- **Responses:**
  - `200` with the updated vehicle payload (status becomes `"departed"`).
  - `404` when the vehicle has not signed in.

#### GET /api/vehicle/vehicle
- **Description:** Fetch a single vehicle by plate.
- **Query parameters:** `vehiclePlate` *(string, required)*.
- **Response example (200):** same structure as the sign-in response.
- **Errors:** `404` when no record exists.

#### GET /api/vehicle/vehicles
- **Description:** List vehicles filtered by status and/or date.
- **Query parameters:**
  - `status` *(string, optional)* – `arrived` or `departed`.
  - `date` *(string, optional)* – filter by Jakarta calendar day (`YYYY-MM-DD`). Applies to arrival time unless `status=departed`.
- **Response example (200):**
```json
{
  "ok": true,
  "vehicles": [
    {
      "vehiclePlate": "B1234XYZ",
      "status": "departed",
      "arriveTime": "2025-01-12T07:45:00+07:00",
      "departTime": "2025-01-12T09:15:00+07:00",
      "...": "..."
    }
  ]
}
```
- **Errors:** `400` for invalid status or date format.

---

## PM Inventory APIs (`/api/pm`)

These endpoints manage PM locations and DN inventory.

#### POST /api/pm/create-pm
- **Description:** Create a PM location or return the existing entry (case-insensitive match).
- **Request body (application/json):**
```json
{
  "pm_name": "PM Alpha",
  "lng": "106.8200",
  "lat": "-6.1700"
}
```
- **Response example (200):**
```json
{
  "ok": true,
  "created": true,
  "pm": {
    "id": 5,
    "pm_name": "PM Alpha",
    "lng": "106.8200",
    "lat": "-6.1700"
  }
}
```
- **Errors:** `422` (validation) when `pm_name` is blank.

#### GET /api/pm/list_pm
- **Description:** List all PM locations.
- **Response example (200):**
```json
{
  "ok": true,
  "total": 2,
  "items": [
    {"id": 5, "pm_name": "PM Alpha", "lng": "106.8200", "lat": "-6.1700"},
    {"id": 6, "pm_name": "PM Beta", "lng": null, "lat": null}
  ]
}
```

#### POST /api/pm/inbound
- **Description:** Register a DN as inbound to the specified PM (status becomes `"in"`).
- **Request body (application/json):**
```json
{
  "pm_name": "PM Alpha",
  "dn_number": "DN001-20250101"
}
```
- **Response example (200):**
```json
{
  "ok": true,
  "record": {
    "id": 91,
    "pm_name": "PM Alpha",
    "dn_number": "DN001-20250101",
    "status": "in",
    "in_time": "2025-01-12T08:30:00"
  }
}
```
- **Errors:** `400` with message from validation (e.g. invalid DN or PM).

#### POST /api/pm/outbound
- **Description:** Mark the latest inbound record for the DN as outbound (`status="out"`).
- **Request body:** same shape as `/inbound`.
- **Response example (200):**
```json
{
  "ok": true,
  "record": {
    "id": 91,
    "pm_name": "PM Alpha",
    "dn_number": "DN001-20250101",
    "status": "out",
    "out_time": "2025-01-12T11:05:00"
  }
}
```
- **Errors:** `404` when no inbound record is available to close.

#### GET /api/pm/find_dn
- **Description:** Identify which PM currently holds a DN (latest inbound record without outbound).
- **Query parameters:** `dn_number` *(string, required)*.
- **Response example (200):**
```json
{
  "ok": true,
  "pm": {
    "pm_name": "PM Alpha",
    "in_time": "2025-01-12T08:30:00"
  }
}
```
- **When not found:** `{"ok": true, "pm": null}`.

#### GET /api/pm/inventory
- **Description:** List all DN numbers currently stored at a PM.
- **Query parameters:** `pm_name` *(string, required)*.
- **Response example (200):**
```json
{
  "ok": true,
  "pm_name": "PM Alpha",
  "total": 2,
  "items": [
    {"id": 91, "dn_number": "DN001-20250101", "in_time": "2025-01-12T08:30:00"},
    {"id": 94, "dn_number": "DN004-20250101", "in_time": null}
  ]
}
```

---

## Error handling quick reference

| HTTP | Typical causes | Body |
|------|----------------|------|
| 400  | Validation errors (invalid DN, missing required fields, inconsistent filters). | `{"detail": "..."}`
| 404  | Resource not found (DN record, vehicle, PM inventory entry). | `{"detail": "..."}`
| 500  | Unexpected exceptions (Google API errors, sync failures, missing Mapbox token). | `{"ok": false, "error": "...", "errorInfo": "...stack..."}` |

The backend does not implement authentication or rate limiting. Integrate it behind your API gateway if those features are required.

**路径参数**:
- `dn_number` (string): DN 号码

**响应**:
```json
{
  "ok": true,
  "data": {
    "dn_number": "DN001",
    "status": "ON THE WAY",
    "status_delivery": "On the way",
    "plan_mos_date": "01 Jan 25",
    "lsp": "LSP Name",
    "region": "Jakarta",
    "lng": "106.8456",
    "lat": "-6.2088",
    ...
  }
}
```

**错误响应**:
```json
{
  "detail": "DN not found"
}
```
Status: 404

---

### 4. DN 批量查询

#### GET /api/dn/batch

批量获取多个 DN 信息。

**查询参数**:
- `dn_numbers` (string): 逗号分隔的 DN 号码列表，如 `"DN001,DN002,DN003"`

**响应**:
```json
{
  "ok": true,
  "data": [
    {
      "dn_number": "DN001",
      ...
    },
    {
      "dn_number": "DN002",
      ...
    }
  ]
}
```

---

### 5. DN 搜索 (简单)

#### GET /api/dn/search

简单的 DN 号码搜索。

**查询参数**:
- `dn_number` (string): DN 号码 (支持部分匹配)

**响应**:
```json
{
  "ok": true,
  "data": [
    {
      "dn_number": "DN001",
      ...
    }
  ]
}
```

---

### 6. DN 更新

#### POST /api/dn/update

更新 DN 状态和信息 (支持照片上传)。

**Content-Type**: `multipart/form-data`

**表单参数**:
- `dnNumber` (string, 必需): DN 号码
- `status` (string, 必需): 新状态
- `delivery_status` (string, 可选): 配送状态 (自动规范化)
- `status_delivery` (string, 可选): 配送状态别名 (向后兼容)
- `remark` (string, 可选): 备注
- `photo` (file, 可选): 照片文件
- `lng` (string/float, 可选): 经度
- `lat` (string/float, 可选): 纬度
- `updated_by` (string, 可选): 更新人

**有效状态值**:
- `PREPARE VEHICLE`
- `ON THE WAY`
- `ON SITE`
- `POD`
- `REPLAN MOS PROJECT`
- `WAITING PIC FEEDBACK`
- `REPLAN MOS DUE TO LSP DELAY`
- `CLOSE BY RN`
- `CANCEL MOS`
- `NO STATUS`
- `NEW MOS`
- `ARRIVED AT WH`
- `TRANSPORTING FROM WH`
- `ARRIVED AT XD/PM`
- `TRANSPORTING FROM XD/PM`
- `ARRIVED AT SITE`
- `开始运输`
- `运输中`
- `已到达`
- `过夜`

**响应**:
```json
{
  "ok": true,
  "id": 123,
  "photo": "https://bucket.s3.amazonaws.com/uploads/photo.jpg",
  "delivery_status_update_result": {
    "sheet": "Plan MOS 2025",
    "row": 10,
    "updated": true
  },
  "timestamp_update_result": {
    "sheet": "Plan MOS 2025",
    "row": 10,
    "column": 19,
    "column_name": "actual_arrive_time_ata",
    "new_value": "10/2/2025 7:10:00",
    "status": "ARRIVED AT SITE",
    "updated": true
  }
}
```

**响应字段说明**:
- `delivery_status_update_result`: 配送状态同步结果
- `timestamp_update_result`: 时间戳写入结果
  - `column`: 列位置 (18 = R列, 19 = S列)
  - `column_name`: 列名称
  - `new_value`: 写入的时间戳值
  - `status`: 触发写入的状态

**说明**:
- 自动创建 DN 记录历史
- 如果提供坐标和照片,同时存储
- 如果 DN 在 Google Sheets 中,自动同步 `status_delivery` 回表格
- **自动写入时间戳到 Google Sheet**:
  - **到达时间戳** (写入 **S 列** `actual_arrive_time_ata`):
    - `ARRIVED AT SITE` - 到达 site
    - `ARRIVED AT XD/PM` - 到达 XD/PM
  - **出发时间戳** (写入 **R 列** `actual_depart_from_start_point_atd`):
    - `TRANSPORTING FROM WH` - 从 WH 出发
    - `TRANSPORTING FROM XD/PM` - 从 XD/PM 出发
  - 其他状态（如 `POD`、`ON THE WAY` 等）不会触发时间戳写入
  - 时间格式: `M/D/YYYY H:MM:SS` (GMT+7),例如: `10/2/2025 7:10:00`
  - 同步逻辑也会将相同时间戳写入数据库字段 `actual_depart_from_start_point_atd` / `actual_arrive_time_ata`
  - 状态匹配不区分大小写
- 所有修改的单元格会自动添加:
  - 备注: "Modified by Fast Tracker ({updated_by})" （显示操作者名称）
  - 链接: https://idnsc.dpdns.org/admin

**重要提示**:
- 上传 `POD` 状态时，建议同时提供 `delivery_status="POD"` 以保持数据一致性
- 如果不提供 `delivery_status`，系统会根据 `status` 自动设置:
  - `status="ARRIVED AT SITE"` → `delivery_status="On Site"`
  - 其他状态 → `delivery_status="On the way"`

---

### 7. DN 批量创建

#### POST /api/dn/batch_update

批量创建新的 DN 记录。

**Content-Type**: `application/json`

**请求体**:
```json
{
  "dn_numbers": ["DN001", "DN002", "DN003"]
}
```

**响应**:
```json
{
  "status": "ok",
  "success_count": 2,
  "failure_count": 1,
  "success_dn_numbers": ["DN001", "DN002"],
  "failure_details": {
    "DN003": "DN number 已存在"
  }
}
```

**错误类型**:
- `"无效的 DN number"`: DN 格式不正确
- `"DN number 已存在"`: DN 已在数据库中
- `"请求中重复"`: 同一请求中有重复的 DN

---

### 8. DN 记录编辑

#### PUT /api/dn/update/{id}

更新指定 DN 历史记录。

**Content-Type**: `multipart/form-data` 或 `application/json`

**路径参数**:
- `id` (int): 记录 ID

**表单/JSON 参数**:
- `status` (string, 可选): 状态
- `remark` (string, 可选): 备注
- `updated_by` (string, 可选): 更新人
- `photo` (file, 可选): 照片

**响应**:
```json
{
  "ok": true,
  "id": 123
}
```

---

### 9. DN 记录删除

#### DELETE /api/dn/update/{id}

删除指定 DN 历史记录。

**路径参数**:
- `id` (int): 记录 ID

**响应**:
```json
{
  "ok": true
}
```

---

### 10. DN 软删除

#### DELETE /api/dn/{dn_number}

软删除指定 DN (设置 `is_deleted = "Y"`)。

**路径参数**:
- `dn_number` (string): DN 号码

**响应**:
```json
{
  "ok": true
}
```

**说明**: 
- 软删除的 DN 不会在列表查询中显示
- 数据仍保留在数据库中,可以通过 Google Sheets 同步恢复

---

### 11. DN 记录历史

#### GET /api/dn/records

获取 DN 的历史记录列表。

**查询参数**:
- `dn_number` (string, 可选): DN 号码
- `page` (int, 可选): 页码，默认 1
- `page_size` (int, 可选): 每页数量，默认 20

**响应**:
```json
{
  "ok": true,
  "total": 5,
  "page": 1,
  "page_size": 20,
  "items": [
    {
      "id": 123,
      "dn_number": "DN001",
      "status": "ON THE WAY",
      "remark": "Driver called",
      "photo_url": "https://...",
      "lng": "106.8456",
      "lat": "-6.2088",
      "created_at": "2025-01-01T08:00:00+07:00",
      "updated_by": "admin"
    }
  ]
}
```

---

### 12. DN 批量列表

#### GET /api/dn/list/batch

批量获取多个 DN 的完整信息。

**查询参数**:
- `dn_numbers` (string): 逗号分隔的 DN 号码

**响应**:
```json
{
  "ok": true,
  "data": [...]
}
```

---

### 13. 统计接口 - 按日期

#### GET /api/dn/stats/{date}

获取指定日期的 DN 状态统计。

**路径参数**:
- `date` (string): 日期，格式 `"01 Jan 25"` 或 `"2025-01-01"`

**响应**:
```json
{
  "ok": true,
  "data": [
    {
      "group": "Total",
      "date": "01 Jan 25",
      "values": [10, 25, 15, 5, 8, 3, 2, 1, 1, 70]
    }
  ]
}
```

**说明**: `values` 数组按以下顺序排列:
1. Prepare Vehicle
2. On the way
3. On Site
4. POD
5. Waiting PIC Feedback
6. RePlan MOS due to LSP Delay
7. RePlan MOS Project
8. Cancel MOS
9. Close by RN
10. 总计

---

### 14. 统计接口 - 状态配送

#### GET /api/dn/status-delivery/stats

获取 DN 状态配送统计和 LSP 汇总。

**查询参数**:
- `lsp` (string, 可选): 物流服务商筛选
- `plan_mos_date` (string, 可选): 计划日期，默认当前日期 (雅加达时间)

**响应**:
```json
{
  "ok": true,
  "data": [
    {
      "status_delivery": "On the way",
      "count": 25
    },
    {
      "status_delivery": "On Site",
      "count": 15
    },
    {
      "status_delivery": "POD",
      "count": 30
    }
  ],
  "total": 70,
  "lsp_summary": [
    {
      "lsp": "LSP Alpha",
      "total_dn": 40,
      "status_not_empty": 35
    },
    {
      "lsp": "LSP Beta",
      "total_dn": 30,
      "status_not_empty": 25
    }
  ]
}
```

**说明**:
- `total_dn`: LSP 的 DN 总数 (仅统计状态为 On the way, On Site, POD 的 DN)
- `status_not_empty`: 有状态的 DN 数量

---

### 15. LSP 汇总历史记录

#### GET /api/dn/status-delivery/lsp-summary-records

获取 LSP 汇总的历史数据 (每小时捕获一次)。

**查询参数**:
- `lsp` (string, 可选): 物流服务商筛选
- `limit` (int, 可选): 返回记录数，默认 5000，范围 1-10000

**响应**:
```json
{
  "ok": true,
  "data": {
    "by_plan_mos_date": [
      {
        "id": 1,
        "lsp": "LSP Alpha",
        "total_dn": 40,
        "status_not_empty": 35,
        "plan_mos_date": "01 Jan 25",
        "recorded_at": "2025-01-01T08:00:00+07:00"
      }
    ],
    "by_update_date": [
      {
        "id": 1,
        "lsp": "LSP Alpha",
        "updated_dn": 40,
        "update_date": "01 Jan 25",
        "recorded_at": "2025-01-01 08:00:00"
      },
      {
        "id": 2,
        "lsp": "NO_LSP",
        "updated_dn": 12,
        "update_date": "01 Jan 25",
        "recorded_at": "2025-01-01 09:00:00"
      }
    ]
  }
}
```

**说明**:
- `by_plan_mos_date`: 仍然返回按小时捕获的 LSP 快照，数据按 `recorded_at` 降序排列 (最新的在前)
- `by_update_date`: 基于每条 DN 的最新记录时间 (换算为雅加达时区并取整到小时) 计算的累计 DN 数
  - `updated_dn`: 截至该小时为止某 LSP 的累计 DN 数量
  - `update_date`: 雅加达当地日期 (格式 `%d %b %y`)
  - `recorded_at`: 雅加达当地小时 (格式 `YYYY-MM-DD HH:mm:ss`)
- 每天从 `00:00` 开始补齐到参考小时，缺少数据的小时返回 0
- 如果中间时段没有新增记录，会自动沿用上一小时的累计值
- 跨越新的一天时会重新从 0 开始累计，避免沿用上一天的值
- 默认返回最近 5000 条 Plan MOS 快照；`by_update_date` 始终返回所有符合条件的最新数据

---

### 16. 筛选选项

#### GET /api/dn/filters

获取所有可用的筛选选项 (LSP、区域、状态等)。

**响应**:
```json
{
  "ok": true,
  "data": {
    "lsp": ["LSP Alpha", "LSP Beta", "LSP Gamma"],
    "region": ["Jakarta", "Bandung", "Surabaya"],
    "status": ["ON THE WAY", "ON SITE", "POD"],
    "status_delivery": ["On the way", "On Site", "POD"],
    "total": 150
  }
}
```

---

### 17. 数据同步

#### POST /api/dn/sync

手动触发与 Google Sheets 的数据同步。

**响应**:
```json
{
  "ok": true,
  "synced": 150,
  "created": 5,
  "updated": 10,
  "ignored": 135
}
```

**说明**:
- 自动同步每 5 分钟执行一次
- 同步过程包括:
  1. 从 Google Sheets 读取所有 "Plan MOS" 工作表
  2. 数据规范化处理
  3. 与数据库比对
  4. 增量更新
  5. 软删除不在表格中的 DN

---

### 18. 同步日志

#### GET /api/dn/sync/log/latest

获取最近的同步日志。

**响应**:
```json
{
  "ok": true,
  "data": {
    "id": 123,
    "started_at": "2025-01-01T08:00:00+07:00",
    "ended_at": "2025-01-01T08:00:15+07:00",
    "synced_count": 150,
    "error_message": null
  }
}
```

---

### 19. 同步日志文件

#### GET /api/dn/sync/log/file

下载完整的同步日志文件。

**响应**: 文本文件 (Content-Type: text/plain)

---

### 20. 动态列扩展

#### POST /api/dn/extend

扩展 DN 表的动态列。

**Content-Type**: `application/json`

**请求体**:
```json
{
  "column_name": "new_field",
  "column_type": "string"
}
```

**响应**:
```json
{
  "ok": true,
  "message": "Column added successfully"
}
```

**支持的列类型**:
- `string` / `text`
- `integer` / `int`
- `float` / `decimal`
- `boolean` / `bool`
- `date`
- `datetime`

---

### 21. 数据归档标记

#### POST /api/dn/mark

标记超过指定天数的 POD 记录为归档状态 (灰色文本)。

**Content-Type**: `application/json`

**请求体**:
```json
{
  "threshold_days": 7
}
```

**响应**:
```json
{
  "ok": true,
  "threshold_days": 7,
  "threshold_date": "2024-12-25",
  "matched_rows": 50,
  "formatted_rows": 50,
  "sheets_processed": ["Plan MOS 2025", "Plan MOS 2024"],
  "affected_rows": [
    {
      "sheet": "Plan MOS 2025",
      "row": 10,
      "plan_mos_date": "20 Dec 24",
      "status_delivery": "POD",
      "formatted": true
    }
  ]
}
```

**说明**:
- 仅标记 `status_delivery = "POD"` 且 `plan_mos_date` 超过阈值的行
- 默认阈值为 7 天
- 直接在 Google Sheets 中修改文本颜色为灰色

---

## 车辆管理接口

### 1. 车辆签到

#### POST /api/vehicle/signin

登记车辆到达信息。

**Content-Type**: `application/json`

**请求体**:
```json
{
  "vehiclePlate": "B1234ABC",
  "lsp": "LSP Alpha",
  "vehicleType": "Truck",
  "driverName": "John Doe",
  "driverContact": "081234567890",
  "arriveTime": "2025-01-01T08:00:00+07:00"
}
```

**响应**:
```json
{
  "ok": true,
  "data": {
    "vehiclePlate": "B1234ABC",
    "lsp": "LSP Alpha",
    "vehicleType": "Truck",
    "driverName": "John Doe",
    "contactNumber": "081234567890",
    "LSP": "LSP Alpha",
    "status": "arrived",
    "arriveTime": "2025-01-01T08:00:00+07:00",
    "departTime": null,
    "createdAt": "2025-01-01T08:00:00+07:00",
    "updatedAt": "2025-01-01T08:00:00+07:00"
  }
}
```

**说明**:
- 车牌号作为唯一标识
- 如果车辆已签到,会更新现有记录
- 自动设置状态为 `"arrived"`

---

### 2. 车辆离场

#### POST /api/vehicle/depart

登记车辆离场时间。

**Content-Type**: `application/json`

**请求体**:
```json
{
  "vehiclePlate": "B1234ABC",
  "departTime": "2025-01-01T18:00:00+07:00"
}
```

**响应**:
```json
{
  "ok": true,
  "data": {
    "vehiclePlate": "B1234ABC",
    "status": "departed",
    "arriveTime": "2025-01-01T08:00:00+07:00",
    "departTime": "2025-01-01T18:00:00+07:00",
    ...
  }
}
```

**错误响应**:
```json
{
  "detail": "Vehicle not found"
}
```
Status: 404

---

### 3. 车辆单个查询

#### GET /api/vehicle/vehicle

查询单个车辆信息。

**查询参数**:
- `vehicle_plate` (string, 必需): 车牌号

**响应**:
```json
{
  "ok": true,
  "data": {
    "vehiclePlate": "B1234ABC",
    "lsp": "LSP Alpha",
    "status": "arrived",
    ...
  }
}
```

---

### 4. 车辆列表查询

#### GET /api/vehicle/vehicles

查询车辆列表 (支持筛选)。

**查询参数**:
- `status` (string, 可选): 状态筛选 (`"arrived"` / `"departed"`)
- `date` (string, 可选): 日期筛选，格式 `"2025-01-01"`
- `lsp` (string, 可选): LSP 筛选

**响应**:
```json
{
  "ok": true,
  "data": [
    {
      "vehiclePlate": "B1234ABC",
      "lsp": "LSP Alpha",
      "status": "arrived",
      "arriveTime": "2025-01-01T08:00:00+07:00",
      ...
    }
  ]
}
```

---

## 数据模型

### DN (Delivery Note)

```typescript
interface DN {
  dn_number: string;           // DN 号码 (主键)
  status: string;              // 当前状态
  status_delivery: string;     // 配送状态 (自动规范化)
  plan_mos_date: string;       // 计划日期 "01 Jan 25"
  lsp: string;                 // 物流服务商
  region: string;              // 区域
  remark: string;              // 备注
  photo_url: string;           // 照片 URL
  lng: string;                 // 经度
  lat: string;                 // 纬度
  last_updated_by: string;     // 最后更新人
  gs_sheet: string;            // Google Sheet 名称
  gs_row: number;              // 行号
  is_deleted: "Y" | "N";       // 软删除标记
  // ... 其他动态列
}
```

### DNRecord (DN 历史记录)

```typescript
interface DNRecord {
  id: number;                  // 记录 ID (主键)
  dn_number: string;           // DN 号码 (外键)
  status: string;              // 状态
  remark: string;              // 备注
  photo_url: string;           // 照片 URL
  lng: string;                 // 经度
  lat: string;                 // 纬度
  created_at: string;          // 创建时间 (ISO 8601)
  updated_by: string;          // 更新人
}
```

### Vehicle (车辆)

```typescript
interface Vehicle {
  vehicle_plate: string;       // 车牌号 (主键)
  lsp: string;                 // 物流服务商
  vehicle_type: string;        // 车辆类型
  driver_name: string;         // 司机姓名
  contact_number: string;      // 联系电话
  status: "arrived" | "departed"; // 状态
  arrive_time: string;         // 到达时间 (ISO 8601)
  depart_time: string;         // 离场时间 (ISO 8601)
  created_at: string;          // 创建时间
  updated_at: string;          // 更新时间
}
```

### StatusDeliveryLspStat (LSP 统计快照)

```typescript
interface StatusDeliveryLspStat {
  id: number;                  // ID (主键)
  lsp: string;                 // 物流服务商
  total_dn: number;            // DN 总数 (仅统计状态为 On the way, On Site, POD 的 DN)
  status_not_empty: number;    // 有状态的 DN 数
  plan_mos_date: string;       // 计划日期
  recorded_at: string;         // 记录时间 (ISO 8601)
}
```

---

## 错误码

### HTTP 状态码

| 状态码 | 说明 |
|-------|------|
| 200 | 成功 |
| 400 | 请求参数错误 |
| 404 | 资源不存在 |
| 500 | 服务器内部错误 |

### 业务错误码

| 错误码 | 说明 |
|-------|------|
| `invalid_dn_number` | DN 号码格式无效 |
| `invalid_status` | 状态值无效 |
| `dn_not_found` | DN 不存在 |
| `vehicle_not_found` | 车辆不存在 |
| `internal_error` | 内部错误 |

---
