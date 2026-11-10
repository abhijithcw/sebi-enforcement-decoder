from sed.entity import entity_id, guess_kind, mask_pan, normalise


def test_normalise_strips_noise():
    assert normalise("M/s. Zee Entertainment Enterprises Ltd. (Noticee No. 2)") == "ZEE ENTERTAINMENT ENTERPRISES"
    assert normalise("Shri Ramesh Kumar") == "RAMESH KUMAR"
    assert normalise("ABC Securities Private Limited") == "ABC SECURITIES"
    assert normalise("ABC Securities Pvt Ltd") == normalise("A.B.C. Securities Private Limited")


def test_entity_id_precedence():
    assert entity_id(cin="L12345MH2000PLC123456", pan="ABCDE1234F", name="X Ltd") == "cin:L12345MH2000PLC123456"
    assert entity_id(isin="INE123A01016", name="X Ltd") == "isin:INE123A01016"
    assert entity_id(pan="ABCDE1234F", name="Some Person") == "pan:ABCDE1234F"
    assert entity_id(name="Some Person") == "name:SOME PERSON"


def test_mask_pan():
    assert mask_pan("ABCDE1234F") == "ABC******F"
    assert mask_pan(None) is None


def test_guess_kind():
    assert guess_kind("Foo Bar Ltd") == "company"
    assert guess_kind("Ramesh Kumar") == "individual"
