FROM ghcr.io/oras-project/oras:v1.4.0@sha256:5fabb4e523de1781443c227fca62782a51aeacd044413c07f54dc64935d7f013 AS plugins

# Marmot downloads all 71 core plugins at start. This image holds only the ones we use and starts with that turned off.
# Plugins are pinned by digest; the registry stores each binary gzipped.
WORKDIR /plugins
RUN oras pull --platform linux/arm64 ghcr.io/marmotdata/plugins/postgresql@sha256:0836db48db04c461382592843a873141d996bf5fce90269fc6e0d757b7a504d2 \
    && oras pull --platform linux/arm64 ghcr.io/marmotdata/plugins/dbt@sha256:256fc695328076910f149c2df0873359b463a7709dae960a0cba20e308197035 \
    && for f in marmot-plugin-*; do gunzip -c "$f" > "$f.bin" && mv "$f.bin" "$f" && chmod 755 "$f"; done

FROM ghcr.io/marmotdata/marmot:0.14.0-distroless@sha256:e42382dd440c290f6110894c2997cd7dba992a66e5e9796fa89696861d061bce

COPY --from=plugins /plugins /plugins
ENV MARMOT_PLUGINS_DIR=/plugins \
    MARMOT_PLUGINS_AUTOINSTALL=false
