"""Use OS trust and public roots, never bypass certificate checks."""
import ssl, os, time
from pathlib import Path


def context():
    try:
        import truststore
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except (ImportError, RuntimeError, NotImplementedError):
        return public_context()


def public_context():
    ctx=ssl.create_default_context()
    try:
        import certifi
        ctx.load_verify_locations(cafile=certifi.where())
    except (ImportError,OSError):pass
    return ctx


def certificate_error(exc):
    reason=getattr(exc,'reason',exc)
    return isinstance(reason,ssl.SSLCertVerificationError) or 'CERTIFICATE_VERIFY_FAILED' in str(reason).upper()


def child_bundle(directory):
    """For subprocess libraries (requests/git) using PEM instead of native trust.
    Include only system roots trusted by Windows for server authentication.
    Honor an explicit CA configuration from the parent instead.
    """
    if os.name!='nt':return None
    import certifi
    dest=Path(directory)/'avs-trusted-ca.pem'
    if dest.exists() and time.time()-dest.stat().st_mtime<86400:return dest
    data=Path(certifi.where()).read_bytes()+b'\n'
    for cert,encoding,trust in ssl.enum_certificates('ROOT'):
        if encoding=='x509_asn' and (trust is True or '1.3.6.1.5.5.7.3.1' in trust):
            data+=ssl.DER_cert_to_PEM_cert(cert).encode('ascii')
    import tempfile
    with tempfile.NamedTemporaryFile(dir=directory,delete=False) as f:
        f.write(data);tmp=Path(f.name)
    try:os.replace(tmp,dest)
    finally:tmp.unlink(missing_ok=True)
    return dest
