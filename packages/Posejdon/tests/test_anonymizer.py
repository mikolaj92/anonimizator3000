from posejdon import GlinerRecognizer, TextAnonymizer


def test_anonymizer_redacts_presidio_and_polish_regex_entities() -> None:
    anonymizer = TextAnonymizer()

    result = anonymizer.anonymize(
        "Jan Kowalski ma PESEL 44051401359, NIP 526-104-08-28, REGON 123456785, "
        "email jan@example.com, telefon +48 501 202 303 i konto PL61109010140000071219812874."
    )

    assert "Jan Kowalski" not in result.text
    assert "44051401359" not in result.text
    assert "526-104-08-28" not in result.text
    assert "123456785" not in result.text
    assert "jan@example.com" not in result.text
    assert "501 202 303" not in result.text
    assert "PL61109010140000071219812874" not in result.text
    assert "<PL_PERSON_NAME>" in result.text
    assert "<PL_PESEL>" in result.text
    assert "<EMAIL_ADDRESS>" in result.text
    assert result.findings["PL_PERSON_NAME"] == 1
    assert result.findings["PL_PESEL"] == 1
    assert result.findings["PL_NIP"] == 1
    assert result.findings["PL_REGON"] == 1
    assert result.findings["EMAIL_ADDRESS"] == 1


def test_anonymizer_redacts_polish_surname_with_context() -> None:
    anonymizer = TextAnonymizer()

    result = anonymizer.anonymize("Pacjenta Jana Kowalskiego przyjęła Pani Nowak.")

    assert "Nowak" not in result.text
    assert "Jana Kowalskiego" not in result.text
    assert result.findings["PL_SURNAME"] == 2


def test_anonymizer_redacts_polish_domain_specific_regex_sets() -> None:
    anonymizer = TextAnonymizer()

    result = anonymizer.anonymize(
        "Data urodzenia: 14.05.1944. Miejsce urodzenia: Kraków. "
        "Adres: ul. Marszałkowska 10/12, 00-001 Warszawa. "
        "Szpital Uniwersytecki w Krakowie. Urząd Skarbowy Warszawa Wola. "
        "ACME Polska sp. z o.o. Sygn. akt II K 123/24. Nr umowy ABC/12/2024."
    )

    assert "14.05.1944" not in result.text
    assert "Miejsce urodzenia: Kraków" not in result.text
    assert "ul. Marszałkowska 10/12" not in result.text
    assert "Szpital Uniwersytecki" not in result.text
    assert "Urząd Skarbowy" not in result.text
    assert "ACME Polska sp. z o.o." not in result.text
    assert "II K 123/24" not in result.text
    assert "ABC/12/2024" not in result.text
    assert result.findings["PL_BIRTH_DATE"] == 1
    assert result.findings["PL_BIRTH_PLACE"] == 1
    assert result.findings["PL_ADDRESS"] == 1
    assert result.findings["PL_MEDICAL_FACILITY"] == 1
    assert result.findings["PL_PUBLIC_OFFICE"] == 1
    assert result.findings["PL_COMPANY"] == 1
    assert result.findings["PL_CASE_NUMBER"] == 1
    assert result.findings["PL_CONTRACT_NUMBER"] == 1


def test_validated_identifiers_reject_bad_checksums() -> None:
    anonymizer = TextAnonymizer()

    result = anonymizer.anonymize("Błędny PESEL 12345678901 i błędny NIP 5250007422.")

    assert "PL_PESEL" not in result.findings
    assert "PL_NIP" not in result.findings


def test_gliner_recognizer_maps_predictions_to_presidio_results() -> None:
    class FakeModel:
        def predict_entities(self, text, labels, threshold):
            return [
                {"label": "person", "start": 0, "end": 12, "score": 0.91},
                {"label": "organization", "start": 16, "end": 24, "score": 0.82},
            ]

    recognizer = GlinerRecognizer("fake", 0.45, model=FakeModel())

    results = recognizer.analyze(
        "Anna Nowak w ACME SA",
        ["PL_PERSON_NAME", "ORGANIZATION"],
        None,
    )

    assert [(result.entity_type, result.start, result.end) for result in results] == [
        ("PL_PERSON_NAME", 0, 12),
        ("ORGANIZATION", 16, 24),
    ]
