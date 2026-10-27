FROM ghcr.io/obot-platform/mcp-oauth-proxy:latest@sha256:60cc919bab30ea630b6139894dc8112455e447894e94204eb9641019cdeb97bd

USER root
RUN apk add --no-cache socat
COPY --chmod=755 .config/dev/external-mcp-proxy/dev-entrypoint.sh /app/dev-entrypoint.sh
USER oauth

ENTRYPOINT ["/app/dev-entrypoint.sh"]
CMD ["./oauth-proxy"]
