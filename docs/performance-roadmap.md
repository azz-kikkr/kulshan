# Kulshan Performance Roadmap

Post-launch performance improvements for regional inventory packs.

## Context

Default mode (`kulshan report`) runs cost-only using global Cost Explorer APIs. Fast (~30s).

Regional packs (security, sweep, dr, drift, pulse, limit, topo) are opt-in. They scan AWS services per-region and can be slow in large environments.

## Phase 2 (first priority after launch)

Low-risk fixes. Remove obvious waste before adding concurrency.

### 2.1 Botocore Config on all pack-created clients

Packs create their own boto3 clients without timeout config. Add:

```python
from botocore.config import Config
config = Config(connect_timeout=5, read_timeout=15, retries={"max_attempts": 2, "mode": "standard"})
client = session.client("ec2", region_name=region, config=config)
```

Files: every scanner that calls `session.client()`.

### 2.2 Remove IAM service-last-accessed polling

`checks/security/scanner/iam.py` has `_check_service_last_accessed` that:
- Calls `generate_service_last_accessed_details` per role
- Polls with `time.sleep(1)` up to 8 times per role
- Samples 10 roles

Potential delay: 82 seconds of sleeping.

Fix: Disable by default. Make opt-in via a `--deep-iam` flag or similar.

### 2.3 Fix security/network triple region loop

`checks/security/scanner/network.py` loops regions in `scan()`, then loops regions AGAIN in:
- `_check_blackhole_routes()`
- `_check_vpc_endpoints()`
- `_check_wide_open_nacls()`
- `_check_vpn_tunnels()`

That's 5 separate region iterations. Should be 1 pass collecting all data, then analysis.

### 2.4 Fix sweep/compute duplicate describe_volumes

`checks/sweep/scanner/compute.py` calls `describe_volumes` twice per region:
1. Once filtered for `status=available` (unattached)
2. Once unfiltered to build `live_vol_ids` for orphan snapshot detection

Fix: One call, filter locally.

### 2.5 Add per-pack timing

After each pack completes, log duration. Show in final summary.

## Phase 3 (after Phase 2 is measured)

Bounded region-level concurrency.

```python
with ThreadPoolExecutor(max_workers=4) as pool:
    futures = {pool.submit(scan_region, region): region for region in regions}
    for future in as_completed(futures, timeout=pack_timeout):
        region = futures[future]
        try:
            result = future.result(timeout=30)
        except Exception as exc:
            record_region_skip(region, exc)
```

Only implement after Phase 2 removes the easy waste. Concurrency hides bad patterns.

## Phase 4 (architectural, post v0.2)

Shared inventory cache. Discover resources once, feed to all packs.

```python
inventory = {
    region: {
        instances, volumes, load_balancers, rds_instances,
        nat_gateways, security_groups, stacks
    }
}
```

Plugin model for optional diagnostic packs.

## API call estimates (current, unoptimized)

| Pack | Calls per region | 1 region | 3 regions | 17 regions |
|------|-----------------|----------|-----------|------------|
| cost | 0 (global) | 10-15 | 10-15 | 10-15 |
| tag | 0-1 (global) | 3-5 | 3-5 | 3-5 |
| security | ~10 + IAM global | ~20 | ~50 | ~170+ |
| sweep | ~7-15 (nested) | ~20 | ~45-60 | ~120-255 |
| dr | ~6-10 | ~15 | ~30-45 | ~100-170 |
| drift | ~3-5 | ~5 | ~15 | ~50-85 |
| pulse | ~5-8 | ~10 | ~25 | ~85-136 |
| limit | ~20-50 | ~30-50 | ~70-150 | ~340-850 |
| topo | ~8-12 | ~12 | ~30 | ~136-204 |
| age | ~5-8 | ~10 | ~25 | ~85-136 |
