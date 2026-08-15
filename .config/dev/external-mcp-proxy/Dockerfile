FROM ghcr.io/obot-platform/mcp-oauth-proxy:latest

USER root
RUN apk add --no-cache socat
COPY --chmod=755 .config/dev/external-mcp-proxy/dev-entrypoint.sh /app/dev-entrypoint.sh
USER oauth

ENTRYPOINT ["/app/dev-entrypoint.sh"]
CMD ["./oauth-proxy"]
