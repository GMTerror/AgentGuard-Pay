import base64
import hashlib
import io
import tarfile
import pytest
from dashboard.get_enterprise import MEMBER, extract, verify


def make_tgz(files):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_integrity_check():
    data = b"hello"
    good = "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode()
    assert (
        verify(good, data)
        and not verify(good, b"tampered")
        and not verify("sha1-abc", data)
    )


def test_extracts_only_the_bundle(tmp_path):
    path = extract(make_tgz({MEMBER: b"js", "package/other.txt": b"x"}), str(tmp_path))
    assert open(path, "rb").read() == b"js" and [
        p.name for p in tmp_path.iterdir()
    ] == ["ag-grid-enterprise.min.noStyle.js"]


def test_missing_member_is_an_error(tmp_path):
    with pytest.raises(KeyError):
        extract(make_tgz({"package/other.txt": b"x"}), str(tmp_path))
