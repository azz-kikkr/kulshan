# Kulshan IAM Setup

Kulshan is read-only. CUR/Data Export workflows use customer-owned billing exports and do not write to AWS.

The current S3-related commands are:

- `kulshan cur s3-check --s3 s3://BILLING_BUCKET_NAME/EXPORT/PREFIX/`: readiness check only. It downloads nothing and runs no analysis.
- `kulshan investigate cost --s3 s3://BILLING_BUCKET_NAME/EXPORT/PREFIX/ --month YYYY-MM`: experimental S3-native cost investigation. It reads the manifest and queries CUR/Data Export Parquet through DuckDB `httpfs`.

The experimental S3-native investigation path does not download the full CUR by default. It requires no Athena workgroup, has no Glue catalog dependency, and has no Athena scanned-data billing for the default evidence workflow. Standard S3 request and transfer charges may still apply.

Local/offline mode remains supported with `kulshan cur validate --path ./cur/` and `kulshan investigate ec2 --cur ./cur/ --month YYYY-MM`. Local validation and local EC2 investigation require no AWS IAM permissions.

## KulshanCurS3ReadOnly Add-On Policy

Replace `BILLING_BUCKET_NAME` and `EXPORT/PREFIX/` with the customer billing export bucket and prefix.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ListCurExportPrefix",
      "Effect": "Allow",
      "Action": "s3:ListBucket",
      "Resource": "arn:aws:s3:::BILLING_BUCKET_NAME",
      "Condition": {
        "StringLike": {
          "s3:prefix": [
            "EXPORT/PREFIX/*",
            "EXPORT/PREFIX/"
          ]
        }
      }
    },
    {
      "Sid": "ReadCurExportObjects",
      "Effect": "Allow",
      "Action": "s3:GetObject",
      "Resource": "arn:aws:s3:::BILLING_BUCKET_NAME/EXPORT/PREFIX/*"
    }
  ]
}
```

`kulshan cur s3-check` uses `ListObjectsV2` and `HeadObject` for one manifest and one Parquet object when found. AWS authorizes `HeadObject` with `s3:GetObject`; the readiness check does not download object bodies.

`kulshan investigate cost --s3` uses the same read-only S3 scope to read the manifest and query Parquet through DuckDB `httpfs`. It should be treated as an experimental S3-native investigation path, not as a production-complete product.

## Optional KulshanDataExportsDiscoveryReadOnly Policy

Kulshan does not currently discover AWS Data Exports. If a future workflow adds discovery, an administrator can consider a separate read-only policy like this:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ListBillingDataExports",
      "Effect": "Allow",
      "Action": [
        "bcm-data-exports:ListExports",
        "bcm-data-exports:GetExport"
      ],
      "Resource": "*"
    }
  ]
}
```

This is not required for `s3-check` or the current experimental S3-native cost investigation command when the bucket and prefix are already known.

## Optional KMS Decrypt Policy

If the S3 bucket or objects use a customer-managed KMS key, `HeadObject` or Parquet reads through DuckDB `httpfs` may require decrypt permission depending on the encryption setup and account controls.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "DecryptCurExportObjects",
      "Effect": "Allow",
      "Action": "kms:Decrypt",
      "Resource": "arn:aws:kms:REGION:123456789012:key/KEY_ID",
      "Condition": {
        "StringEquals": {
          "kms:ViaService": "s3.REGION.amazonaws.com"
        }
      }
    }
  ]
}
```

## Admin CLI Verification

Confirm the identity:

```bash
aws sts get-caller-identity
```

Confirm the prefix is listable:

```bash
aws s3api list-objects-v2 \
  --bucket BILLING_BUCKET_NAME \
  --prefix EXPORT/PREFIX/ \
  --max-keys 50
```

Confirm object metadata is readable without downloading data:

```bash
aws s3api head-object \
  --bucket BILLING_BUCKET_NAME \
  --key EXPORT/PREFIX/path/to/Manifest.json

aws s3api head-object \
  --bucket BILLING_BUCKET_NAME \
  --key EXPORT/PREFIX/path/to/file.parquet
```

Then run Kulshan's readiness check:

```bash
kulshan cur s3-check --s3 s3://BILLING_BUCKET_NAME/EXPORT/PREFIX/
```

Run experimental S3-native cost investigation for a billing month:

```bash
kulshan investigate cost --s3 s3://BILLING_BUCKET_NAME/EXPORT/PREFIX/ --month YYYY-MM
```

For local/offline validation, manually copy a small known Parquet object locally and run:

```bash
aws s3 cp s3://BILLING_BUCKET_NAME/EXPORT/PREFIX/path/to/file.parquet ./.kulshan-real-cur-test/file.parquet
kulshan cur validate --path ./.kulshan-real-cur-test/
```

`kulshan cur validate` validates generic CUR readability and should not fail just because EC2 rows are absent. EC2 investigation is one pack, not the validation gate. `kulshan investigate ec2` still uses local files only and can fail clearly when the selected data has no EC2 rows.