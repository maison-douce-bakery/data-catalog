# data-catalog

Runtime image of the data catalog: the upstream [Marmot](https://github.com/marmotdata/marmot) image plus only the plugins we use (`postgresql`, `dbt`), so it starts without downloading all 71 core plugins.

- Marmot, the plugins and the build tool are pinned by digest in the `Dockerfile`. To upgrade, change the version and digest together.
- CI builds the image on every PR and pushes it to ECR `mdb/marmot` from `main`.

## Pipelines

`pipelines/` holds the Marmot ingestion pipeline definitions (no secrets; connection values are placeholders). `scripts/register_postgres_pipeline.py` fills them from environment variables and creates or updates the scheduled pipeline through the Marmot API, using only the Python standard library. On a fresh Marmot it also sets the admin password from `MARMOT_ADMIN_PASSWORD`, but only while the account still has the seeded default login and a forced change. It never triggers a run, creates API keys or runs enrichment. Run it with `python scripts/register_postgres_pipeline.py` and the `MARMOT_*` variables listed in its docstring.
