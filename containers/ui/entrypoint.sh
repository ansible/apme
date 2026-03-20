#!/bin/sh
# Extract the nameserver from /etc/resolv.conf and inject it into the nginx
# config template. Works on both Docker (127.0.0.11) and Podman (aardvark-dns
# at the network gateway IP).
DNS_RESOLVER=$(awk '/^nameserver/{print $2; exit}' /etc/resolv.conf)
DNS_RESOLVER="${DNS_RESOLVER:-127.0.0.11}"

export DNS_RESOLVER
envsubst '${DNS_RESOLVER}' \
  < /etc/nginx/conf.d/default.conf.template \
  > /etc/nginx/conf.d/default.conf

exec nginx -g 'daemon off;'
