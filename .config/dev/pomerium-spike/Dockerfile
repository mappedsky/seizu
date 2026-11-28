# Pomerium Core for the delegated-OAuth spike (see README.md beside this file).
#
# The upstream image is distroless, so the binary is copied onto Debian to gain
# a shell, socat (the localhost:9000 forwarder, AUTH-002) and openssl (the
# local CA the entrypoint generates on first start).
FROM pomerium/pomerium:v0.34.2@sha256:d1ffb3189cd1975ca272660f22869e5f553bc3cc3c60e4bfd16f1a9d3fcf759e AS pomerium

FROM debian:trixie-slim@sha256:8aa5a7bf5a1955bddbe7d74fcdd0b91ab30e74362a8b73af672b0481b35b44a0
RUN apt-get update \
  && apt-get install -y --no-install-recommends ca-certificates openssl socat \
  && rm -rf /var/lib/apt/lists/*
COPY --from=pomerium /bin/pomerium /bin/pomerium
COPY --from=pomerium /pomerium/LICENSE /pomerium/LICENSE
COPY --chmod=755 .config/dev/pomerium-spike/entrypoint.sh /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
CMD ["/bin/pomerium", "--config", "/pomerium/config.yaml"]
