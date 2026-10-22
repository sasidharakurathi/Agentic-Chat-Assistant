# Caddy with the DNS plugins a certificate needs when the server is not
# reachable from the internet (Phase 8.2, plan D15). Caddy proves it owns
# the name by writing one TXT record through the DNS provider's API, so
# ports 80 and 443 never have to be open to the world.
#
# Built in: acme-dns (works with any DNS provider: one CNAME record hands
# over only the certificate check) and Cloudflare. Another provider is one
# more `--with` line: https://github.com/caddy-dns
#
#   docker build -f deploy/proxy/caddy.Dockerfile -t assistant-studio-proxy:2.10 deploy/proxy
#
# then CADDY_IMAGE=assistant-studio-proxy:2.10 in .env.production. Rebuild
# monthly (OPERATIONS §7): this image does not update itself, and Caddy's
# security fixes arrive through it. Pin the two base images by digest once
# you have built one you trust (`docker image inspect caddy:2.10`).

ARG CADDY_VERSION=2.10

FROM caddy:${CADDY_VERSION}-builder AS build
RUN xcaddy build \
    --with github.com/caddy-dns/acmedns \
    --with github.com/caddy-dns/cloudflare

FROM caddy:${CADDY_VERSION}
COPY --from=build /usr/bin/caddy /usr/bin/caddy
# Fails the build, not the first start, if a plugin went missing.
RUN caddy list-modules | grep -q "dns.providers.acmedns" \
    && caddy list-modules | grep -q "dns.providers.cloudflare"
