#!/bin/sh
# Certbot deploy hook; update the key before publishing the new certificate.
set -eu
: "${RENEWED_LINEAGE:?Certbot must provide RENEWED_LINEAGE}"
certificate_directory=/etc/t7-game-server/tls
install -d -m 750 -o root -g t7-tls "$certificate_directory"
install -m 640 -o root -g t7-tls "$RENEWED_LINEAGE/privkey.pem" "$certificate_directory/.privkey.pem.new"
install -m 644 -o root -g t7-tls "$RENEWED_LINEAGE/fullchain.pem" "$certificate_directory/.fullchain.pem.new"
mv "$certificate_directory/.privkey.pem.new" "$certificate_directory/privkey.pem"
mv "$certificate_directory/.fullchain.pem.new" "$certificate_directory/fullchain.pem"
