#!/usr/bin/env bash
set -euo pipefail

cert_dir="$(cd "$(dirname "$0")" && pwd)/certs"
mkdir -p "$cert_dir"
cd "$cert_dir"

if [[ -f server.crt && -f server.key && -f ca.crt ]]; then
    exit 0
fi

cat > ca.cnf <<'EOF'
[req]
distinguished_name = dn
[dn]
[v3_ca]
basicConstraints = critical, CA:TRUE
keyUsage = critical, keyCertSign, cRLSign
subjectKeyIdentifier = hash
EOF

cat > server.ext <<'EOF'
basicConstraints = CA:FALSE
keyUsage = critical, digitalSignature
extendedKeyUsage = serverAuth
subjectAltName = DNS:wspap.okx.com, DNS:www.okx.com
authorityKeyIdentifier = keyid
EOF

openssl ecparam -name prime256v1 -genkey -noout -out ca.key
openssl req -x509 -new -key ca.key -sha256 -days 365 -subj "/CN=trading-engine harness CA" \
    -config ca.cnf -extensions v3_ca -out ca.crt

openssl ecparam -name prime256v1 -genkey -noout -out server.key
openssl req -new -key server.key -subj "/CN=wspap.okx.com" -out server.csr
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial -days 365 -sha256 \
    -extfile server.ext -out server.crt

rm -f ca.cnf server.ext server.csr ca.srl
chmod 600 ca.key server.key
