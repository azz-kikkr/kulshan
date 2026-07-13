"""Auto-discover CUR/Data Export configurations via AWS APIs."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class CurExportInfo:
    """Information about a discovered CUR/Data Export."""
    
    export_name: str
    export_arn: str
    s3_bucket: str
    s3_prefix: str
    format: str  # "PARQUET" or "TEXT_OR_CSV"
    status: str  # "HEALTHY", "UNHEALTHY"
    
    @property
    def s3_uri(self) -> str:
        """Return s3://bucket/prefix URI."""
        prefix = self.s3_prefix.rstrip("/")
        return f"s3://{self.s3_bucket}/{prefix}/"


def discover_cur_exports(session: Any) -> list[CurExportInfo]:
    """Discover CUR/Data Exports configured in the account.
    
    Uses the bcm-data-exports API (ListExports + GetExport) to find
    configured exports and their S3 destinations.
    
    Returns an empty list if:
    - No exports configured
    - Access denied (missing bcm-data-exports:ListExports permission)
    - API errors
    
    This is a best-effort discovery — failure should not block the scan.
    """
    exports: list[CurExportInfo] = []
    
    try:
        client = session.client("bcm-data-exports", region_name="us-east-1")
        
        # List all exports
        paginator = client.get_paginator("list_exports")
        export_arns: list[str] = []
        
        for page in paginator.paginate():
            for export in page.get("Exports", []):
                arn = export.get("ExportArn")
                if arn:
                    export_arns.append(arn)
        
        # Get details for each export
        for arn in export_arns:
            try:
                resp = client.get_export(ExportArn=arn)
                export_data = resp.get("Export", {})
                
                name = export_data.get("Name", "unknown")
                status = export_data.get("ExportStatus", {}).get("StatusCode", "UNKNOWN")
                
                # Extract S3 destination
                dest = export_data.get("DestinationConfigurations", {})
                s3_dest = dest.get("S3Destination", {})
                bucket = s3_dest.get("S3Bucket", "")
                prefix = s3_dest.get("S3Prefix", "")
                output_format = s3_dest.get("S3OutputConfigurations", {}).get("Format", "PARQUET")
                
                if bucket:
                    exports.append(CurExportInfo(
                        export_name=name,
                        export_arn=arn,
                        s3_bucket=bucket,
                        s3_prefix=prefix,
                        format=output_format,
                        status=status,
                    ))
            except Exception:
                # Skip individual export errors
                continue
                
    except Exception:
        # Discovery is best-effort — return empty on any error
        pass
    
    return exports


def find_best_cur_export(session: Any) -> Optional[CurExportInfo]:
    """Find the best available CUR export for analysis.
    
    Prioritizes:
    1. HEALTHY status
    2. PARQUET format (faster queries)
    
    Returns None if no suitable export found.
    """
    exports = discover_cur_exports(session)
    
    if not exports:
        return None
    
    # Filter to healthy exports
    healthy = [e for e in exports if e.status == "HEALTHY"]
    if not healthy:
        healthy = exports  # Fall back to any export
    
    # Prefer Parquet format
    parquet = [e for e in healthy if e.format == "PARQUET"]
    if parquet:
        return parquet[0]
    
    return healthy[0] if healthy else None


def check_cur_s3_access(session: Any, export: CurExportInfo) -> bool:
    """Quick check if we can access the CUR S3 location.
    
    Does a lightweight ListObjectsV2 with MaxKeys=1 to verify access
    without downloading data.
    """
    try:
        s3 = session.client("s3")
        s3.list_objects_v2(
            Bucket=export.s3_bucket,
            Prefix=export.s3_prefix,
            MaxKeys=1,
        )
        return True
    except Exception:
        return False
