"""Unit tests for throway/contract — Limits + Errors als Daten (1.53.0).

Der Slice aus Review-Kandidat 5: die dünnen Wahrheiten (Live-Werte,
Error-Tabelle) haben ein Home mit Renderern pro Fläche. Diese Tests
pinnen die Vertragsteile, die drift-gefährdet sind: jede HELP-Platzhalter
ist in limits(), die Error-Daten sind vollständig und konsistent
gerendert, API.md ist frisch. Die HTTP-Suite deckt das Verhalten ab."""
import json
import os
import re
import subprocess
import sys
import tempfile

import pytest

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _HERE)

os.environ.setdefault("THROWAWAY_ROOT", tempfile.mkdtemp(prefix="test-contract-"))

from throway import contract                      # noqa: E402
import store                                      # noqa: E402


def test_limits_cover_every_help_placeholder():
    """Jeder {PLATZHALTER} in irgendeinem HELP-Body (store, pics, retain,
    dirs) muss in limits() existieren — sonst wirft _render_help_body
    KeyError, schlimmer: still bleibt er unsubstituiert stehen."""
    lim = contract.limits()
    used = set()
    for t in store.HELP.values():
        used |= set(re.findall(r"\{([A-Z][A-Z0-9_]+)\}", t["body"]))
    missing = used - set(lim)
    assert not missing, f"Platzhalter ohne limits()-Schluessel: {sorted(missing)}"


def test_limits_values_match_config():
    lim = contract.limits()
    assert lim["TTL_HOURS"] == store.TTL_HOURS
    assert lim["MAX_FILE_MB"] == store.MAX_FILE // (1024 * 1024)
    assert lim["PUBLIC_BASE"] == store.PUBLIC_BASE


def test_errors_are_the_nine_stable_codes():
    errs = contract.errors()
    assert len(errs) == 9
    codes = [e["code"] for e in errs]
    assert len(set(codes)) == 9
    assert {e["http"] for e in errs} == {400, 401, 403, 404, 411, 413, 429, 507, 501}


def test_render_errors_help_complete_and_substituted():
    body = contract.render_errors_help(contract.limits())
    for e in contract.errors():
        assert e["code"] in body
        assert str(e["http"]) in body
    assert not re.search(r"\{[A-Z][A-Z0-9_]+\}", body), "unsubstituierter Platzhalter"


def test_errors_payload_matches_data():
    lim = contract.limits()
    p = contract.errors_payload(lim)
    assert set(p) == {e["code"] for e in contract.errors()}
    assert p["too_large"]["meaning"] == \
        f"file > {lim['MAX_FILE_MB']}MB (pics: {lim['PICS_MAX_FILE_MB']}MB)"
    assert p["rate_limited"]["meaning"] == \
        f"too many requests ({lim['RATE_LIMIT']}/min/IP)"


def test_substitute_leaves_unknown_placeholders():
    lim = {"MAX_FILE_MB": 5}
    assert contract.substitute("MAX {MAX_FILE_MB}MB", lim) == "MAX 5MB"
    # Literal-Braces (wie in der get_dir_history-Note) ueberleben:
    assert contract.substitute("{ts,file,action,bytes}", lim) == "{ts,file,action,bytes}"


def test_api_spec_has_errors_block():
    """Der additive /api-Block existiert und ist JSON-serialisierbar."""
    p = contract.errors_payload(contract.limits())
    json.dumps(p)  # wirft bei Nicht-serialisierbarem


def test_api_md_is_fresh():
    """release-check-Spiegel als Test: die gen-Region von API.md muss dem
    Generator-Output entsprechen (Hand-Edits an Zahlen = rot)."""
    r = subprocess.run([sys.executable, "scripts/gen-api-md.py", "--check"],
                       cwd=_HERE, capture_output=True, text=True)
    assert r.returncode == 0, f"API.md veraltet:\n{r.stdout}{r.stderr}"
