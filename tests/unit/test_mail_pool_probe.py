"""Unit tests for mail_pool_probe (no live Microsoft calls)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mail_pool_probe import (
    STATUS_ABUSE_MODE,
    STATUS_GRANT_EXPIRED,
    STATUS_NETWORK_ERROR,
    STATUS_OK,
    STATUS_PARSE_ERROR,
    STATUS_REFRESH_INVALID,
    STATUS_UNKNOWN,
    classify_error,
    filter_by_domains,
    is_quarantinable,
    load_pool,
    parse_credential_line,
    pool_stats,
    probe_one,
    probe_sample,
    quarantine,
    sample_accounts,
    update_refresh_token_in_pool,
)


def _line(email: str, password: str = "pw", cid: str = "cid", rt: str = "rt") -> str:
    return f"{email}----{password}----{cid}----{rt}\n"


def test_parse_credential_line_ok():
    c = parse_credential_line("a@hotmail.com----p----client----refresh\n")
    assert c is not None
    assert c.email == "a@hotmail.com"
    assert c.password == "p"
    assert c.client_id == "client"
    assert c.refresh_token == "refresh"
    assert c.domain == "hotmail.com"


def test_parse_credential_line_json_object():
    from mail_pool_probe import normalize_credential_line, normalize_credential_text

    raw = (
        '{"email":"KanodeShapleigh20@outlook.com","password":"Di5VBneA",'
        '"clientId":"9e5f9abc-0000-0000-0000-000000000001",'
        '"refreshToken":"M.C501_BAY.0.U.-refresh"}'
    )
    c = parse_credential_line(raw, line_no=7)
    assert c is not None
    assert c.email == "KanodeShapleigh20@outlook.com"
    assert c.password == "Di5VBneA"
    assert c.client_id.startswith("9e5f9abc")
    assert c.refresh_token.startswith("M.C501")
    assert c.domain == "outlook.com"
    norm = normalize_credential_line(raw)
    assert norm == (
        "KanodeShapleigh20@outlook.com----Di5VBneA----"
        "9e5f9abc-0000-0000-0000-000000000001----M.C501_BAY.0.U.-refresh"
    )
    text, stats = normalize_credential_text(raw)
    assert stats["written"] == 1 and stats["json_objects"] == 1
    assert text.strip() == norm


def test_parse_credential_line_rejects_bad():
    assert parse_credential_line("") is None
    assert parse_credential_line("# comment") is None
    assert parse_credential_line("only----two") is None
    assert parse_credential_line("notanemail----p----c----r") is None
    assert parse_credential_line("a@b.com----p---- ----r") is None
    assert parse_credential_line('{"email":"x","password":"p"}') is None


def test_normalize_alt_delimiters_and_csv():
    from mail_pool_probe import normalize_credential_line, normalize_credential_text

    pipe = "a@outlook.com|pw1|cid-aaaa|rt-bbbbbbbb"
    assert normalize_credential_line(pipe) == "a@outlook.com----pw1----cid-aaaa----rt-bbbbbbbb"

    colon = "b@hotmail.com:pw2:cid-bbbb:rt-cccccccc"
    assert normalize_credential_line(colon) == "b@hotmail.com----pw2----cid-bbbb----rt-cccccccc"

    tab = "c@live.com\tpw3\tcid-cccc\trt-dddddddd"
    assert normalize_credential_line(tab) == "c@live.com----pw3----cid-cccc----rt-dddddddd"

    # wrapped JSON list
    wrapped = json.dumps(
        {
            "data": [
                {
                    "email": "w@outlook.com",
                    "password": "p",
                    "client_id": "cid-wrap1",
                    "refresh_token": "rt-wrap111",
                }
            ]
        }
    )
    text, stats = normalize_credential_text(wrapped)
    assert stats["written"] == 1 and stats["json_objects"] == 1
    assert "w@outlook.com----p----cid-wrap1----rt-wrap111" in text

    # CSV with header
    csv_body = (
        "Email,Password,ClientId,RefreshToken\n"
        "h@outlook.com,ph,cid-csv01,rt-csv0001\n"
        "bad,no,at,sign\n"
        "i@hotmail.com,pi,cid-csv02,rt-csv0002\n"
    )
    text2, stats2 = normalize_credential_text(csv_body)
    assert stats2["written"] == 2
    assert stats2.get("csv_rows") == 2
    assert "h@outlook.com----ph----cid-csv01----rt-csv0001" in text2
    assert "i@hotmail.com----pi----cid-csv02----rt-csv0002" in text2


def test_normalize_alt_delimiters_and_csv():
    from mail_pool_probe import normalize_credential_line, normalize_credential_text

    pipe = "a@outlook.com|pw1|cid-aaaa|rt-bbbbbbbb"
    assert normalize_credential_line(pipe) == "a@outlook.com----pw1----cid-aaaa----rt-bbbbbbbb"

    colon = "b@hotmail.com:pw2:cid-bbbb:rt-cccccccc"
    assert normalize_credential_line(colon) == "b@hotmail.com----pw2----cid-bbbb----rt-cccccccc"

    tab = "c@live.com\tpw3\tcid-cccc\trt-dddddddd"
    assert normalize_credential_line(tab) == "c@live.com----pw3----cid-cccc----rt-dddddddd"

    # wrapped JSON list
    wrapped = json.dumps(
        {
            "data": [
                {
                    "email": "w@outlook.com",
                    "password": "p",
                    "client_id": "cid-wrap1",
                    "refresh_token": "rt-wrap111",
                }
            ]
        }
    )
    text, stats = normalize_credential_text(wrapped)
    assert stats["written"] == 1 and stats["json_objects"] == 1
    assert "w@outlook.com----p----cid-wrap1----rt-wrap111" in text

    # CSV with header
    csv_body = (
        "Email,Password,ClientId,RefreshToken\n"
        "h@outlook.com,ph,cid-csv01,rt-csv0001\n"
        "bad,no,at,sign\n"
        "i@hotmail.com,pi,cid-csv02,rt-csv0002\n"
    )
    text2, stats2 = normalize_credential_text(csv_body)
    assert stats2["written"] == 2
    assert stats2.get("csv_rows") == 2
    assert "h@outlook.com----ph----cid-csv01----rt-csv0001" in text2
    assert "i@hotmail.com----pi----cid-csv02----rt-csv0002" in text2


def test_load_pool_dedupes(tmp_path: Path):
    p = tmp_path / "mail_credentials.txt"
    p.write_text(
        _line("A@Hotmail.com", "p1", "c1", "r1")
        + _line("a@hotmail.com", "p2", "c2", "r2")
        + _line("b@outlook.com", "p3", "c3", "r3")
        + "# skip\n"
        + "badline\n",
        encoding="utf-8",
    )
    accs = load_pool(p)
    assert len(accs) == 2
    assert accs[0].email == "A@Hotmail.com"
    assert accs[0].password == "p1"
    assert accs[1].email == "b@outlook.com"


def test_scan_and_compact_pool(tmp_path: Path):
    from mail_pool_probe import compact_pool, pool_stats, scan_pool_file

    p = tmp_path / "mail_credentials.txt"
    p.write_text(
        _line("A@Hotmail.com", "p1", "c1", "r1")
        + _line("a@hotmail.com", "p2", "c2", "r2")  # dup
        + _line("b@outlook.com", "p3", "c3", "r3")
        + "# keep-me\n"
        + "not-a-cred\n"
        + _line("B@Outlook.com", "p4", "c4", "r4")  # case dup of b
        + "\n",
        encoding="utf-8",
    )
    scan = scan_pool_file(p)
    assert scan["parsed_lines"] == 4
    assert scan["unique"] == 2
    assert scan["duplicate_extra"] == 2
    assert scan["invalid_lines"] == 1
    assert scan["needs_compact"] is True

    st = pool_stats(p)
    assert st["total"] == 2
    assert st["duplicate_extra"] == 2
    assert st["needs_compact"] is True
    assert "p1" not in str(st)

    preview = compact_pool(p, dry_run=True)
    assert preview["dry_run"] is True
    assert preview["changed"] is True
    assert preview["duplicate_extra"] == 2
    assert preview["invalid_dropped"] == 1
    assert preview["unique"] == 2
    assert preview["backup_path"] is None
    # dry_run must not rewrite
    assert "not-a-cred" in p.read_text(encoding="utf-8")
    assert p.read_text(encoding="utf-8").count("hotmail.com") >= 1

    out = compact_pool(p, dry_run=False, drop_invalid=True, drop_comments=False)
    assert out["changed"] is True
    assert out["backup_path"]
    assert Path(out["backup_path"]).is_file()
    text = p.read_text(encoding="utf-8")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    # first wins + comment kept + invalid gone
    assert "A@Hotmail.com----p1----c1----r1" in text
    assert "b@outlook.com----p3----c3----r3" in text
    assert "a@hotmail.com----p2" not in text
    assert "not-a-cred" not in text
    assert "# keep-me" in text
    assert "p2" not in text  # dup dropped
    assert out["unique"] == 2
    assert "已精简" in out["summary"]

    again = compact_pool(p, dry_run=False)
    assert again["changed"] is False
    assert again["duplicate_extra"] == 0
    assert "无需精简" in again["summary"]


def test_scan_and_compact_pool(tmp_path: Path):
    from mail_pool_probe import compact_pool, pool_stats, scan_pool_file

    p = tmp_path / "mail_credentials.txt"
    p.write_text(
        _line("A@Hotmail.com", "p1", "c1", "r1")
        + _line("a@hotmail.com", "p2", "c2", "r2")  # dup
        + _line("b@outlook.com", "p3", "c3", "r3")
        + "# keep-me\n"
        + "not-a-cred\n"
        + _line("B@Outlook.com", "p4", "c4", "r4")  # case dup of b
        + "\n",
        encoding="utf-8",
    )
    scan = scan_pool_file(p)
    assert scan["parsed_lines"] == 4
    assert scan["unique"] == 2
    assert scan["duplicate_extra"] == 2
    assert scan["invalid_lines"] == 1
    assert scan["needs_compact"] is True

    st = pool_stats(p)
    assert st["total"] == 2
    assert st["duplicate_extra"] == 2
    assert st["needs_compact"] is True
    assert "p1" not in str(st)

    preview = compact_pool(p, dry_run=True)
    assert preview["dry_run"] is True
    assert preview["changed"] is True
    assert preview["duplicate_extra"] == 2
    assert preview["invalid_dropped"] == 1
    assert preview["unique"] == 2
    assert preview["backup_path"] is None
    # dry_run must not rewrite
    assert "not-a-cred" in p.read_text(encoding="utf-8")
    assert p.read_text(encoding="utf-8").count("hotmail.com") >= 1

    out = compact_pool(p, dry_run=False, drop_invalid=True, drop_comments=False)
    assert out["changed"] is True
    assert out["backup_path"]
    assert Path(out["backup_path"]).is_file()
    text = p.read_text(encoding="utf-8")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    # first wins + comment kept + invalid gone
    assert "A@Hotmail.com----p1----c1----r1" in text
    assert "b@outlook.com----p3----c3----r3" in text
    assert "a@hotmail.com----p2" not in text
    assert "not-a-cred" not in text
    assert "# keep-me" in text
    assert "p2" not in text  # dup dropped
    assert out["unique"] == 2
    assert "已精简" in out["summary"]

    again = compact_pool(p, dry_run=False)
    assert again["changed"] is False
    assert again["duplicate_extra"] == 0
    assert "无需精简" in again["summary"]


def test_filter_by_domains_and_sample():
    from mail_pool_probe import Credential

    accs = [
        Credential("a@hotmail.com", "p", "c", "r"),
        Credential("b@outlook.com", "p", "c", "r"),
        Credential("c@live.com", "p", "c", "r"),
    ]
    assert len(filter_by_domains(accs, None)) == 3
    assert len(filter_by_domains(accs, [])) == 3
    hot = filter_by_domains(accs, ["Hotmail.com"])
    assert [a.email for a in hot] == ["a@hotmail.com"]
    multi = filter_by_domains(accs, ["hotmail.com", "live.com"])
    assert {a.domain for a in multi} == {"hotmail.com", "live.com"}

    s1 = sample_accounts(accs, 2, seed=42)
    s2 = sample_accounts(accs, 2, seed=42)
    assert [a.email for a in s1] == [a.email for a in s2]
    assert len(sample_accounts(accs, 10, seed=1)) == 3
    assert sample_accounts(accs, 0) == []


def test_classify_error_mapping():
    assert classify_error("AADSTS700082: The refresh token has expired") == STATUS_GRANT_EXPIRED
    assert classify_error("grant is expired") == STATUS_GRANT_EXPIRED
    assert classify_error("invalid_grant: The provided grant is invalid") == STATUS_REFRESH_INVALID
    assert classify_error("AADSTS70000: something") == STATUS_REFRESH_INVALID
    assert classify_error("account is in abuse mode") == STATUS_ABUSE_MODE
    assert classify_error("Connection timed out") == STATUS_NETWORK_ERROR
    assert classify_error("URLError: Name or service not known") == STATUS_NETWORK_ERROR
    assert classify_error(TimeoutError("timed out")) == STATUS_NETWORK_ERROR
    assert classify_error("missing fields") == STATUS_PARSE_ERROR
    assert classify_error("something weird") == STATUS_UNKNOWN
    assert classify_error(None) == STATUS_UNKNOWN
    # broad English must NOT mis-classify as network / invalid
    assert classify_error("please check your network settings in account") == STATUS_UNKNOWN
    assert classify_error("value is not valid for display") == STATUS_UNKNOWN


def test_is_quarantinable_whitelist():
    assert is_quarantinable(STATUS_GRANT_EXPIRED)
    assert is_quarantinable(STATUS_REFRESH_INVALID)
    assert is_quarantinable(STATUS_ABUSE_MODE)
    assert not is_quarantinable(STATUS_OK)
    assert not is_quarantinable(STATUS_NETWORK_ERROR)
    assert not is_quarantinable(STATUS_UNKNOWN)
    assert not is_quarantinable(STATUS_PARSE_ERROR)


def test_probe_one_with_injectable_refresh():
    from mail_pool_probe import Credential

    acc = Credential("ok@hotmail.com", "p", "c", "r")

    def ok_fn(_a):
        return True, "", None

    def dead_fn(_a):
        return False, "AADSTS700082: The refresh token has expired due to inactivity", None

    def boom_fn(_a):
        raise TimeoutError("connection timed out")

    def two_tuple_ok(_a):
        return True, ""

    r = probe_one(acc, refresh_fn=ok_fn)
    assert r.status == STATUS_OK
    assert r.email == "ok@hotmail.com"
    assert r.quarantinable is False
    public = r.to_public_dict()
    assert "password" not in public
    assert "client_id" not in public
    assert "refresh_token" not in public
    assert public["quarantinable"] is False

    r2 = probe_one(acc, refresh_fn=dead_fn)
    assert r2.status == STATUS_GRANT_EXPIRED
    assert r2.quarantinable is True
    assert "AADSTS700082" in r2.ms_error

    r3 = probe_one(acc, refresh_fn=boom_fn)
    assert r3.status == STATUS_NETWORK_ERROR
    assert r3.quarantinable is False

    r4 = probe_one(acc, refresh_fn=two_tuple_ok)
    assert r4.status == STATUS_OK


def test_probe_sample_order_and_counts(tmp_path: Path):
    p = tmp_path / "mail_credentials.txt"
    lines = "".join(
        _line(f"u{i}@hotmail.com", f"p{i}", f"c{i}", f"r{i}") for i in range(10)
    )
    p.write_text(lines, encoding="utf-8")

    def refresh(acc):
        local = acc.email.split("@", 1)[0]
        n = int(local[1:])
        if n % 2 == 0:
            return True, "", None
        if n % 3 == 0:
            return False, "Connection timed out", None
        return False, "invalid_grant: The provided grant is invalid", None

    out = probe_sample(
        p,
        domains=["hotmail.com"],
        limit=6,
        seed=7,
        concurrency=3,
        refresh_fn=refresh,
        writeback_rotated=False,
    )
    assert out["probed"] == 6
    assert out["ok"] + out["dead"] == 6
    assert "quarantinable" in out
    assert out["quarantinable"] <= out["dead"]
    for row in out["results"]:
        assert set(row.keys()) == {
            "email",
            "domain",
            "status",
            "reason",
            "ms_error",
            "quarantinable",
        }
        assert "password" not in row
        assert "client_id" not in row
        assert "refresh_token" not in row
        if row["status"] == STATUS_NETWORK_ERROR:
            assert row["quarantinable"] is False
        if row["status"] == STATUS_REFRESH_INVALID:
            assert row["quarantinable"] is True


def test_probe_rotation_writeback(tmp_path: Path):
    p = tmp_path / "mail_credentials.txt"
    p.write_text(_line("rot@hotmail.com", "pw", "cid", "old_rt"), encoding="utf-8")

    def refresh(acc):
        assert acc.refresh_token == "old_rt"
        return True, "", "new_rt_rotated"

    out = probe_sample(
        p,
        limit=1,
        seed=1,
        concurrency=1,
        refresh_fn=refresh,
        writeback_rotated=True,
    )
    assert out["ok"] == 1
    text = p.read_text(encoding="utf-8")
    assert "new_rt_rotated" in text
    assert "old_rt" not in text
    # secrets shape preserved
    assert "rot@hotmail.com----pw----cid----new_rt_rotated" in text


def test_update_refresh_token_in_pool(tmp_path: Path):
    p = tmp_path / "mail_credentials.txt"
    p.write_text(
        _line("a@hotmail.com", "p", "c", "r1") + _line("b@hotmail.com", "p", "c", "r2"),
        encoding="utf-8",
    )
    assert update_refresh_token_in_pool(p, "A@Hotmail.com", "r1b") is True
    text = p.read_text(encoding="utf-8")
    assert "a@hotmail.com----p----c----r1b" in text
    assert "b@hotmail.com----p----c----r2" in text
    assert update_refresh_token_in_pool(p, "missing@x.com", "x") is False


def test_pool_stats(tmp_path: Path):
    live = tmp_path / "mail_credentials.txt"
    live.write_text(
        _line("a@hotmail.com") + _line("b@outlook.com") + _line("c@hotmail.com"),
        encoding="utf-8",
    )
    dead = tmp_path / "mail_credentials.dead.txt"
    dead.write_text(
        "x@hotmail.com----p----c----r----probe:grant_expired----2026-07-24T00:00:00Z\n",
        encoding="utf-8",
    )
    st = pool_stats(live, dead_path=dead)
    assert st["total"] == 3
    assert st["by_domain"]["hotmail.com"] == 2
    assert st["by_domain"]["outlook.com"] == 1
    assert st["dead_total"] == 1
    assert "hotmail.com" in st["known_domains"]
    assert "grant_expired" in st["quarantinable_statuses"]
    # no credential secrets in stats payload
    blob = str(st)
    assert "----" not in blob
    assert "password" not in blob


def test_quarantine_backup_and_dead_append(tmp_path: Path):
    live = tmp_path / "mail_credentials.txt"
    live.write_text(
        _line("keep@hotmail.com", "pk", "ck", "rk")
        + _line("dead1@hotmail.com", "pd1", "cd1", "rd1")
        + _line("dead2@outlook.com", "pd2", "cd2", "rd2")
        + _line("also@live.com", "pa", "ca", "ra"),
        encoding="utf-8",
    )
    out = quarantine(
        live,
        ["dead1@hotmail.com", "DEAD2@outlook.com", "missing@msn.com"],
        reason="probe:grant_expired",
    )
    assert out["removed"] == 2
    assert out["not_found"] == 1
    assert out["live_total_after"] == 2
    assert Path(out["backup_path"]).is_file()
    assert "bak-probe-" in Path(out["backup_path"]).name

    remaining = load_pool(live)
    emails = {a.email.lower() for a in remaining}
    assert emails == {"keep@hotmail.com", "also@live.com"}
    keep = next(a for a in remaining if a.email.startswith("keep"))
    assert keep.password == "pk"
    assert keep.refresh_token == "rk"

    dead = Path(out["dead_path"])
    text = dead.read_text(encoding="utf-8")
    assert "dead1@hotmail.com----pd1----cd1----rd1----probe:grant_expired----" in text
    assert "dead2@outlook.com----pd2----cd2----rd2----probe:grant_expired----" in text
    assert "keep@hotmail.com" not in text


def test_quarantine_sanitizes_reason_separators(tmp_path: Path):
    live = tmp_path / "mail_credentials.txt"
    live.write_text(_line("a@hotmail.com", "p", "c", "r"), encoding="utf-8")
    out = quarantine(live, ["a@hotmail.com"], reason="bad----reason\nline")
    dead = Path(out["dead_path"]).read_text(encoding="utf-8")
    # reason must not re-introduce ---- field breaks
    assert "----bad-reason line----" in dead or "bad-reason" in dead
    assert "bad----reason" not in dead


def test_quarantine_rejects_empty_emails(tmp_path: Path):
    live = tmp_path / "mail_credentials.txt"
    live.write_text(_line("a@hotmail.com"), encoding="utf-8")
    with pytest.raises(ValueError):
        quarantine(live, [])
    with pytest.raises(ValueError):
        quarantine(live, ["", "  "])
