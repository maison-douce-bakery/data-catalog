# data-catalog

Runtime image of the data catalog: the upstream [Marmot](https://github.com/marmotdata/marmot) image plus only the plugins we use (`postgresql`, `dbt`), so it starts without downloading all 71 core plugins.

- Marmot, the plugins and the build tool are pinned by digest in the `Dockerfile`. To upgrade, change the version and digest together.
- CI builds the image on every PR and pushes it to ECR `mdb/marmot` from `main`.
