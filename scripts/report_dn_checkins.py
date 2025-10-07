"""Generate DN check-in statistics grouped by plan date, LSP, and region.

This script analyses the `dn` and `dn_record` tables to compute the expected
check-ins (based on delivery status) alongside the actual check-ins recorded
in `dn_record`. The output is an Excel file with one sheet per LSP showing
check-in rates by normalized region.

Regions are normalized to: JABO, WJ, EJBN, Kalimantan, Sumatera based on
keywords found in the original region field.

Usage
=====

    python scripts/report_dn_checkins.py [--output output.xlsx]

The script requires the same environment variables as the main application,
notably ``DATABASE_URL``. The resulting Excel file is saved to the specified
output path (default: dn_checkins_report.xlsx).
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

# Add parent directory to sys.path to enable app imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import DN, DNRecord

STATUS_DELIVERY_EXPECTED = {"pod", "on site", "on the way"}
ARRIVAL_RECORD_STATUSES = {"POD", "ARRIVED AT SITE"}
CUTOFF_DATE = datetime(2025, 9, 26)

# Region normalization mapping
REGION_KEYWORDS = {
    "JABO": "JABO",
    "WJ": "WJ",
    "EJBN": "EJBN",
    "Kalimantan": "Kalimantan",
    "Sumatera": "Sumatera",
}

STANDARD_REGIONS = ["JABO", "WJ", "EJBN", "Kalimantan", "Sumatera"]


@dataclass(slots=True)
class GroupStats:
    expected: int = 0
    actual: int = 0
    arrival: int = 0

    def to_dict(self, plan_mos_date: str, lsp: str, region: str) -> dict[str, object]:
        actual_rate = (self.actual / self.expected) if self.expected else 0.0
        arrival_rate = (self.arrival / self.expected) if self.expected else 0.0
        return {
            "plan_mos_date": plan_mos_date,
            "lsp": lsp,
            "region": region,
            "expected_count": self.expected,
            "actual_count": self.actual,
            "arrival_count": self.arrival,
            "actual_rate": round(actual_rate, 4),
            "arrival_rate": round(arrival_rate, 4),
            "actual_rate_percent": f"{actual_rate * 100:.2f}%",
            "arrival_rate_percent": f"{arrival_rate * 100:.2f}%",
        }


def normalize_label(value: str | None, placeholder: str) -> str:
    if value is None:
        return placeholder
    trimmed = value.strip()
    return trimmed or placeholder


def normalize_region(region: str | None) -> str:
    """Normalize region to one of the standard regions based on keywords."""
    if region is None:
        return "OTHER"
    
    region_upper = region.upper()
    
    for keyword, normalized in REGION_KEYWORDS.items():
        if keyword.upper() in region_upper:
            return normalized
    
    return "OTHER"


def parse_plan_mos_date(date_str: str) -> datetime | None:
    """Parse plan_mos_date string to datetime for filtering."""
    if not date_str:
        return None
    
    formats = [
        "%d %b %y",  # 26 Sep 25
        "%d %b %Y",  # 26 Sep 2025
        "%Y-%m-%d",  # 2025-09-26
        "%d-%m-%Y",  # 26-09-2025
    ]
    
    for fmt in formats:
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except ValueError:
            continue
    
    return None


def fetch_target_dns(session: Session) -> List[Tuple[str, str, str, str]]:
    """Return DN rows relevant for the report (expected check-ins).

    The result is a list of tuples containing (dn_number, lsp, region, plan_mos_date).
    """

    normalized_status = func.lower(func.trim(DN.status_delivery))
    query = (
        session.query(
            DN.dn_number,
            DN.lsp,
            DN.region,
            DN.plan_mos_date,
        )
        .filter(normalized_status.in_(STATUS_DELIVERY_EXPECTED))
    )
    return query.all()


def build_record_lookup(session: Session, dn_numbers: Iterable[str]) -> Dict[str, Tuple[int, int]]:
    """Return a mapping of dn_number to (total_record_count, arrival_record_count)."""

    dn_numbers = list(set(filter(None, dn_numbers)))
    if not dn_numbers:
        return {}

    trimmed_status = func.upper(func.trim(DNRecord.status))
    arrival_case = case((trimmed_status.in_(tuple(ARRIVAL_RECORD_STATUSES)), 1), else_=0)

    rows = (
        session.query(
            DNRecord.dn_number,
            func.count(DNRecord.id).label("total_records"),
            func.coalesce(func.sum(arrival_case), 0).label("arrival_records"),
        )
        .filter(DNRecord.dn_number.in_(dn_numbers))
        .group_by(DNRecord.dn_number)
        .all()
    )

    return {row.dn_number: (int(row.total_records), int(row.arrival_records)) for row in rows}


def compute_stats(session: Session) -> Tuple[List[dict[str, object]], List[str]]:
    """Compute grouped statistics for DN check-ins.
    
    Returns:
        Tuple of (pivot table rows, sorted list of unique regions)
    """

    target_dns = fetch_target_dns(session)
    record_lookup = build_record_lookup(session, (row[0] for row in target_dns))

    # Group by (plan_mos_date, lsp, region)
    groups: Dict[Tuple[str, str, str], GroupStats] = defaultdict(GroupStats)
    all_regions: set[str] = set()

    for dn_number, lsp, region, plan_mos_date in target_dns:
        # Filter by date
        parsed_date = parse_plan_mos_date(plan_mos_date)
        if parsed_date is None or parsed_date < CUTOFF_DATE:
            continue
        
        plan_value = normalize_label(plan_mos_date, "UNKNOWN_PLAN_MOS_DATE")
        lsp_value = normalize_label(lsp, "UNKNOWN_LSP")
        region_value = normalize_region(region)
        
        # Skip OTHER regions
        if region_value == "OTHER":
            continue
        
        all_regions.add(region_value)

        key = (plan_value, lsp_value, region_value)
        stats = groups[key]
        stats.expected += 1

        total_records, arrival_records = record_lookup.get(dn_number, (0, 0))
        if total_records > 0:
            stats.actual += 1
        if arrival_records > 0:
            stats.arrival += 1

    # Convert to pivot table format: each row is (lsp, plan_mos_date) with columns for each region
    pivot_data: Dict[Tuple[str, str], Dict[str, GroupStats]] = defaultdict(dict)
    
    for (plan, lsp, region), stats in groups.items():
        row_key = (lsp, plan)
        pivot_data[row_key][region] = stats
    
    # Use standard region order
    sorted_regions = [r for r in STANDARD_REGIONS if r in all_regions]
    
    # Build output rows
    results = []
    for (lsp, plan), region_stats in sorted(pivot_data.items()):
        # Parse and format plan_mos_date to %Y-%m-%d
        parsed_date = parse_plan_mos_date(plan)
        formatted_date = parsed_date.strftime("%Y-%m-%d") if parsed_date else plan
        
        row = {
            "lsp": lsp,
            "plan_mos_date": formatted_date,
        }
        
        # Add check rate columns for each region
        for region in sorted_regions:
            stats = region_stats.get(region, GroupStats())
            actual_rate = (stats.actual / stats.expected) if stats.expected else 0.0
            arrival_rate = (stats.arrival / stats.expected) if stats.expected else 0.0
            
            row[f"{region}_check_rate"] = f"{actual_rate * 100:.2f}%"
            row[f"{region}_arrival_check_rate"] = f"{arrival_rate * 100:.2f}%"
        
        results.append(row)
    
    return results, sorted_regions


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate DN check-in statistics report")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("dn_checkins_report.xlsx"),
        help="Path to write the output Excel file (default: dn_checkins_report.xlsx)",
    )
    return parser.parse_args(argv)


def write_excel(rows: List[dict[str, object]], regions: List[str], output_path: Path) -> None:
    """Write pivot table to Excel with one sheet per LSP."""
    try:
        import pandas as pd
    except ImportError:
        print("Error: pandas is required for Excel output. Install with: pip install pandas openpyxl", file=sys.stderr)
        raise
    
    # Group rows by LSP
    lsp_data: Dict[str, List[dict[str, object]]] = defaultdict(list)
    for row in rows:
        lsp = row["lsp"]
        lsp_data[lsp].append(row)
    
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        for lsp in sorted(lsp_data.keys()):
            lsp_rows = lsp_data[lsp]
            
            # Build columns
            columns = ["plan_mos_date"]
            for region in regions:
                columns.append(f"{region}_check_rate")
            for region in regions:
                columns.append(f"{region}_arrival_check_rate")
            
            # Create DataFrame
            df_data = []
            for row in lsp_rows:
                row_data = {"plan_mos_date": row["plan_mos_date"]}
                for col in columns[1:]:
                    row_data[col] = row.get(col, "0.00%")
                df_data.append(row_data)
            
            df = pd.DataFrame(df_data, columns=columns)
            
            # Clean sheet name (Excel has 31 char limit and special char restrictions)
            sheet_name = lsp[:31].replace("/", "-").replace("\\", "-").replace("?", "").replace("*", "")
            
            df.to_excel(writer, sheet_name=sheet_name, index=False)


def main(argv: List[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])

    session = SessionLocal()
    try:
        rows, regions = compute_stats(session)
    finally:
        session.close()

    if not rows:
        print("No matching DN records found for the specified criteria.", file=sys.stderr)
        return 0

    write_excel(rows, regions, args.output)
    print(f"Report generated: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
