import pytest

from services.nexuss_bridge.release_ops import FILES, Releases, approval_signature, signature


COMMIT = "a" * 40
IMAGE = "sha256:" + "b" * 64
SECRET = b"test-only-release-signing-key-123456789"
APPROVAL = b"test-only-owner-approval-key-1234567890"


def _executor(tmp_path, *, probe=lambda: True, run=None):
    calls = []

    def fake_run(argv, timeout=25, input=None):
        calls.append(argv)
        if run:
            return run(argv)
        if argv[1:4] == ["-C", str(tmp_path / "source"), "show"]:
            return b"def harmless():\n    return 1\n"
        if argv[1:4] == ["inspect", "-f", "{{.Image}}"]:
            return IMAGE.encode() + b"\n"
        return b""

    service = Releases(SECRET, str(tmp_path / "receipts.db"), approval_secret=APPROVAL,
                       repo=tmp_path / "source", state=tmp_path / "state",
                       run=fake_run, probe=probe)
    return service, calls


def _signed(service, operation, rid, commit=COMMIT, digest="0" * 64):
    stamp = int(service.clock())
    approved = approval_signature(APPROVAL, rid, commit, digest) if operation == "apply" else ""
    return service.execute({"operation": operation, "request_id": rid, "time": stamp,
                            "commit": commit, "digest": digest,
                            "approval_signature": approved,
                            "signature": signature(SECRET, operation, rid, stamp, commit, digest,
                                                   approved)})


def test_exact_release_is_prepared_built_and_verified(tmp_path):
    service, calls = _executor(tmp_path)
    rid = "1" * 32
    prepared = _signed(service, "prepare", rid)
    assert prepared["status"] == "prepared"
    assert prepared["base_image"] == IMAGE
    assert set(prepared["file_hashes"]) == set(FILES)
    assert _signed(service, "prepare", rid)["replayed"] is True
    applied = _signed(service, "apply", rid, digest=prepared["digest"])
    assert (applied["status"], applied["verified"]) == ("deployed_verified", True)
    assert any(argv[:3] == ["/usr/bin/docker", "build", "--pull=false"] for argv in calls)
    assert any("--no-deps" in argv and "dashboard" == argv[-1] for argv in calls)
    assert _signed(service, "apply", rid, digest=prepared["digest"])["replayed"] is True


def test_mismatched_approval_or_signature_cannot_deploy(tmp_path):
    service, calls = _executor(tmp_path)
    rid = "2" * 32
    prepared = _signed(service, "prepare", rid)
    with pytest.raises(ValueError):
        _signed(service, "apply", rid, digest="f" * 64)
    invalid = {"operation": "apply", "request_id": rid, "time": int(service.clock()),
               "commit": COMMIT, "digest": prepared["digest"], "signature": "0" * 64,
               "approval_signature": "0" * 64}
    with pytest.raises(ValueError):
        service.execute(invalid)
    assert not any(argv[1] == "build" for argv in calls)


def test_bridge_key_cannot_authorize_release_without_owner_signature(tmp_path):
    service, calls = _executor(tmp_path)
    rid = "5" * 32
    prepared = _signed(service, "prepare", rid)
    stamp = int(service.clock())
    bad = "0" * 64
    with pytest.raises(ValueError, match="approval"):
        service.execute({"operation": "apply", "request_id": rid, "time": stamp,
                         "commit": COMMIT, "digest": prepared["digest"],
                         "approval_signature": bad,
                         "signature": signature(SECRET, "apply", rid, stamp, COMMIT,
                                                prepared["digest"], bad)})
    assert not any(argv[1] == "build" for argv in calls)


def test_source_change_fails_closed_before_image_mutation(tmp_path):
    contents = [b"a=1\n", b"a=2\n"]
    calls = []

    def fake_run(argv):
        calls.append(argv)
        if argv[1:4] == ["-C", str(tmp_path / "source"), "show"]:
            return contents[0 if len([v for v in calls if "show" in v]) <= 2 else 1]
        if argv[1] == "inspect":
            return IMAGE.encode() + b"\n"
        return b""

    service, _ = _executor(tmp_path, run=fake_run)
    rid = "3" * 32
    prepared = _signed(service, "prepare", rid)
    result = _signed(service, "apply", rid, digest=prepared["digest"])
    assert result["status"] == "precondition_changed"
    assert not any(argv[1] == "build" for argv in calls)


def test_tripped_or_unavailable_dashboard_blocks_prepare(tmp_path):
    service, calls = _executor(tmp_path, probe=lambda: False)
    with pytest.raises(ValueError):
        _signed(service, "prepare", "4" * 32)
    assert not calls
