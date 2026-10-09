from lark.read.parse import Parsed, Section
from lark.read.verify import normalize, quote_found, verify_note
from lark.records import Claim, Evidence, ReadingNote

SOURCE = Parsed("w", "u", "pdf", "0" * 64, "pymupdf", [
    Section("3 Results", "The proposed interval achieves uniform asymp-\ntotic coverage over the parameter space, "
            "including conﬁgurations with exact ties. In simulations with 5 arms, coverage is 94.8% against "
            "87.1% for the naive bootstrap across 1,000 replications."),
])


def _note(claims):
    return ReadingNote(work_id="w", depth="focused", claims=claims)


def test_normalize_handles_ligatures_hyphens_dashes():
    assert normalize("conﬁg—uration  “x”") == 'config-uration "x"'
    assert "asymptotic" in normalize("asymp-\ntotic")


def test_exact_normalized_fuzzy_and_fabricated():
    nt = normalize(SOURCE.full_text)
    assert quote_found("including configurations with exact ties", nt)  # ligature in source
    assert quote_found("achieves uniform asymptotic coverage over the parameter space", nt)  # hyphen break
    # OCR-ish difference: two characters off in a long quote -> fuzzy match
    assert quote_found("In simulatoins with 5 arms, coverage is 94.8% against 87.1% for the naive bootstrap", nt)
    assert not quote_found("The method dominates all baselines by a wide margin in every setting we tried", nt)


def test_verify_note_marks_claims_and_numbers():
    note = _note([
        Claim(text="Coverage is 94.8% vs 87.1% with 5 arms over 1000 replications.",
              evidence=[Evidence(quote="coverage is 94.8% against 87.1% for the naive bootstrap")]),
        Claim(text="Gains of 12.5% over the naive method.", evidence=[Evidence(quote="a twelve point gain everywhere")]),
    ])
    verify_note(note, SOURCE)
    assert note.claims[0].verified and note.claims[0].evidence[0].verified
    assert note.claims[1].verified is False
    v = note.verification
    assert v["n_claims"] == 2 and v["n_verified"] == 1 and v["unsupported_claims"] == [1]
    assert {"claim": 1, "number": "12.5"} in v["unsupported_numbers"]
    assert all(u["claim"] != 0 for u in v["unsupported_numbers"])  # 1000 matches "1,000"


def test_verify_abstract_only_uses_extra_text():
    note = _note([Claim(text="It works.", evidence=[Evidence(quote="we show it works")])])
    verify_note(note, None, extra_text="Here we show it works in theory.")
    assert note.claims[0].verified
