"""Form 4 parsing.

Covered with a real filing's XML shape rather than a live fetch: the parsing is
the part that breaks, and it should be testable without SEC being reachable.
"""

from alphadesk.ingest import insider

# Trimmed from Apple's 2026-08-11 Form 4 (accession 0001140361-26-032884),
# keeping the structure verbatim.
FORM4 = b"""<?xml version="1.0"?>
<ownershipDocument>
  <issuer>
    <issuerName>Apple Inc.</issuerName>
    <issuerTradingSymbol>AAPL</issuerTradingSymbol>
  </issuer>
  <reportingOwner>
    <reportingOwnerId><rptOwnerName>Newstead Jennifer</rptOwnerName></reportingOwnerId>
    <reportingOwnerRelationship>
      <isDirector>0</isDirector>
      <isOfficer>true</isOfficer>
      <officerTitle>SVP, GC and Secretary</officerTitle>
    </reportingOwnerRelationship>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <securityTitle><value>Common Stock</value></securityTitle>
      <transactionDate><value>2026-08-11</value></transactionDate>
      <transactionCoding><transactionCode>S</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>1439</value></transactionShares>
        <transactionPricePerShare><value>307.75</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
      <postTransactionAmounts>
        <sharesOwnedFollowingTransaction><value>40107</value></sharesOwnedFollowingTransaction>
      </postTransactionAmounts>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
  <derivativeTable>
    <derivativeTransaction>
      <securityTitle><value>Restricted Stock Unit</value></securityTitle>
      <transactionDate><value>2026-08-11</value></transactionDate>
      <transactionCoding><transactionCode>A</transactionCode></transactionCoding>
    </derivativeTransaction>
  </derivativeTable>
</ownershipDocument>
"""


def rows():
    return insider._parse_form4(FORM4, "2026-08-13", "https://sec.gov/x/form4.xml")


def test_parses_the_transaction():
    r = rows()[0]
    assert r["symbol"] == "AAPL"
    assert r["company_name"] == "Apple Inc."
    assert r["owner_name"] == "Newstead Jennifer"
    assert r["transaction_date"] == "2026-08-11"
    assert r["securities_transacted"] == 1439
    assert r["transaction_price"] == 307.75
    assert r["securities_owned_after"] == 40107


def test_derivative_rows_are_excluded():
    """A Form 4 carries options/RSU bookkeeping alongside share trades. Only
    the non-derivative table answers 'did an insider buy or sell stock'."""
    assert len(rows()) == 1
    assert all(r["security_type"] == "Common Stock" for r in rows())


def test_codes_are_decoded_not_left_as_letters():
    """'S' and 'D' in an AI answer are unreadable; a reader cannot check them."""
    r = rows()[0]
    assert r["transaction_type"] == "open-market sale"
    assert r["acquisition_or_disposition"] == "disposed"


def test_relationship_flags():
    r = rows()[0]
    assert r["officer"] is True
    assert r["director"] is False          # "0", not absent
    assert r["owner_title"] == "SVP, GC and Secretary"


def test_transaction_value_is_computed():
    assert rows()[0]["transaction_value"] == round(1439 * 307.75, 2)


def test_missing_price_does_not_crash():
    """Option exercises report shares with no price per share."""
    xml = FORM4.replace(b"<transactionPricePerShare><value>307.75</value></transactionPricePerShare>", b"")
    r = insider._parse_form4(xml, "2026-08-13", "u")[0]
    assert r["transaction_price"] is None
    assert r["transaction_value"] is None      # not 0, and not an exception


def test_no_agpl_dependency_remains():
    """The whole reason this module exists: openbb-core/openbb-sec are
    AGPL-3.0-only and this project is MIT."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1]
    for f in ("pyproject.toml", "requirements.txt"):
        assert "openbb" not in (root / f).read_text(), f
