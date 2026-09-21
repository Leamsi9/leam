"""Private, installation-specific TLS identity for the loopback MCP listener."""

import argparse
import ctypes
import errno
import ipaddress
import os
import shutil
import ssl
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


@dataclass(frozen=True)
class Identity:
    certificate: Path
    key: Path


def validate(directory):
    identity = Identity(directory / "certificate.pem", directory / "key.pem")
    try:
        if directory.stat().st_mode & 0o077 or identity.key.stat().st_mode & 0o077:
            raise ValueError("Private TLS file permissions are required")
        cert = x509.load_pem_x509_certificate(identity.certificate.read_bytes())
        now = datetime.now(timezone.utc)
        if not cert.not_valid_before_utc <= now < cert.not_valid_after_utc:
            raise ValueError("Certificate has expired or is not yet valid")
        names = cert.extensions.get_extension_for_class(
            x509.SubjectAlternativeName
        ).value
        if (
            set(names.get_values_for_type(x509.IPAddress))
            != {ipaddress.ip_address("127.0.0.1"), ipaddress.ip_address("::1")}
            or len(names) != 2
        ):
            raise ValueError("Certificate must name only the loopback addresses")
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(identity.certificate, identity.key)
    except (OSError, ValueError, x509.ExtensionNotFound) as error:
        raise ValueError(
            "Local TLS identity is invalid; repair it explicitly before starting"
        ) from error
    return identity


def publish_directory(source, destination):
    """Linux no-replace rename: an existing empty/damaged identity is never replaced."""
    libc = ctypes.CDLL(None, use_errno=True)
    rename = libc.renameat2
    rename.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(source), -100, os.fsencode(destination), 1) != 0:
        code = ctypes.get_errno()
        if code == errno.EEXIST:
            return False
        raise OSError(code, os.strerror(code))
    return True


def provision(data_directory):
    """Create once; do not silently replace an existing or damaged trust identity."""
    data_directory = Path(data_directory)
    data_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination = data_directory / "mcp-tls"
    if destination.exists():
        return validate(destination)
    temporary = Path(tempfile.mkdtemp(prefix=".mcp-tls-", dir=data_directory))
    try:
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Leam local tools")])
        now = datetime.now(timezone.utc)
        certificate = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(now + timedelta(days=397))
            .add_extension(
                x509.BasicConstraints(ca=False, path_length=None), critical=True
            )
            .add_extension(
                x509.SubjectAlternativeName(
                    [
                        x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                        x509.IPAddress(ipaddress.ip_address("::1")),
                    ]
                ),
                critical=False,
            )
            .add_extension(
                x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
            )
            .sign(key, hashes.SHA256())
        )
        for name, content in (
            (
                "key.pem",
                key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                ),
            ),
            ("certificate.pem", certificate.public_bytes(serialization.Encoding.PEM)),
        ):
            fd = os.open(temporary / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as file:
                file.write(content)
                file.flush()
                os.fsync(file.fileno())
        fd = os.open(temporary, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        publish_directory(temporary, destination)
        fd = os.open(data_directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return validate(destination)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()
    identity = provision(args.data_dir)
    print(identity.certificate)
