#!/usr/bin/env python3
"""
Generate (or regenerate) a self-signed SSL certificate for the local HTTPS server.

The certificate covers:
  - DNS: localhost
  - IP:  127.0.0.1
  - IP:  <all detected local network addresses>

Run this script whenever the server's IP address changes, or to replace an
expired certificate.  The output files (cert.pem, key.pem) are placed in the
same directory as this script.

Usage:
  python generate_cert.py
"""

import ipaddress
import os
import socket
import sys
import datetime


def get_local_ips() -> set[str]:
    ips: set[str] = {"127.0.0.1"}
    try:
        hostname = socket.gethostname()
        ips.update(socket.gethostbyname_ex(hostname)[2])
    except Exception:
        pass
    # Connect toward the internet to discover the outgoing interface IP.
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.add(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    return ips


def generate_cert(
    cert_file: str = "cert.pem",
    key_file: str  = "key.pem",
    days: int      = 3650,
) -> bool:
    try:
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
    except ImportError:
        print(
            "[ERROR] The 'cryptography' package is not installed.\n"
            "        Install it with:  pip install cryptography",
            file=sys.stderr,
        )
        return False

    print("Generating self-signed SSL certificate …")

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    local_ips = get_local_ips()
    san_list: list = [x509.DNSName("localhost")]
    for ip_str in sorted(local_ips):
        try:
            san_list.append(x509.IPAddress(ipaddress.ip_address(ip_str)))
        except ValueError:
            pass

    name = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME,         "RobProjector XR"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME,   "RobProjector"),
    ])

    now  = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=days))
        .add_extension(x509.SubjectAlternativeName(san_list), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )

    base_dir = os.path.dirname(os.path.abspath(__file__))

    with open(os.path.join(base_dir, key_file), "wb") as f:
        f.write(key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ))

    with open(os.path.join(base_dir, cert_file), "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))

    san_strs = [
        str(s.value) for s in san_list
    ]
    print(f"  Key  → {key_file}")
    print(f"  Cert → {cert_file}  (valid {days} days)")
    print(f"  SANs : {', '.join(san_strs)}")
    print("\nDone.  Accept the certificate warning in the Meta Quest browser,")
    print("or install cert.pem as a trusted CA in the Quest's certificate store.")
    return True


if __name__ == "__main__":
    ok = generate_cert()
    sys.exit(0 if ok else 1)
