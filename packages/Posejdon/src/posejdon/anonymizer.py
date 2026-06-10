from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from threading import Lock
from typing import Any

import regex
import spacy
from presidio_analyzer import (
    AnalyzerEngine,
    EntityRecognizer,
    Pattern,
    PatternRecognizer,
    RecognizerResult,
)
from presidio_analyzer.nlp_engine import NlpArtifacts, NlpEngine
from presidio_analyzer.recognizer_registry import RecognizerRegistry
from presidio_anonymizer import AnonymizerEngine


@dataclass(frozen=True)
class RegexRecognizerSpec:
    entity: str
    pattern_name: str
    regex: str
    score: float
    context: tuple[str, ...] = ()


POLISH_FIRST_NAMES = (
    "Adam",
    "Adrian",
    "Agnieszka",
    "Aleksandra",
    "Andrzej",
    "Anna",
    "Antoni",
    "Barbara",
    "Bartosz",
    "Beata",
    "Bogdan",
    "Cezary",
    "Damian",
    "Daniel",
    "Dariusz",
    "Dorota",
    "Ewa",
    "Filip",
    "Grzegorz",
    "Hanna",
    "Jakub",
    "Jan",
    "Joanna",
    "Jolanta",
    "Kamil",
    "Karolina",
    "Katarzyna",
    "Kinga",
    "Krzysztof",
    "Łukasz",
    "Magdalena",
    "Małgorzata",
    "Marcin",
    "Marek",
    "Maria",
    "Mariusz",
    "Mateusz",
    "Michał",
    "Monika",
    "Natalia",
    "Paweł",
    "Piotr",
    "Rafał",
    "Robert",
    "Stanisław",
    "Tomasz",
    "Wojciech",
    "Zbigniew",
)

POLISH_FIRST_NAME_FORMS = POLISH_FIRST_NAMES + (
    "Adama",
    "Agnieszki",
    "Aleksandry",
    "Andrzeja",
    "Annę",
    "Anny",
    "Annie",
    "Antoniego",
    "Barbary",
    "Bartosza",
    "Beaty",
    "Bogdana",
    "Cezarego",
    "Damiana",
    "Daniela",
    "Dariusza",
    "Doroty",
    "Ewy",
    "Filipa",
    "Grzegorza",
    "Hanny",
    "Jakuba",
    "Jana",
    "Joanny",
    "Jolanty",
    "Kamila",
    "Karoliny",
    "Katarzyny",
    "Kingi",
    "Krzysztofa",
    "Łukasza",
    "Magdaleny",
    "Małgorzaty",
    "Marcina",
    "Marka",
    "Marii",
    "Mariusza",
    "Mateusza",
    "Michała",
    "Moniki",
    "Natalii",
    "Pawła",
    "Piotra",
    "Rafała",
    "Roberta",
    "Stanisława",
    "Tomasza",
    "Wojciecha",
    "Zbigniewa",
)

POLISH_NAME_PATTERN = (
    r"\b(?:"
    + "|".join(POLISH_FIRST_NAME_FORMS)
    + r")\s+\p{Lu}\p{Ll}{2,}(?:-\p{Lu}\p{Ll}{2,})?\b"
)

POLISH_CONTEXT_SURNAME_PATTERN = (
    r"(?i:\b(?:nazwisko|pan|pani|pacjent(?:a|owi|em|ka)?|klient(?:a|owi|em|ka)?|"
    r"oskarżon(?:y|a|ego|ej)|pow(?:ód|ódka|oda)|pozwany|pozwanej|świadek|dr|mec\.|prof\.)"
    r")[:\s]+(?:\p{Lu}\p{Ll}{2,}\s+){0,2}\p{Lu}\p{Ll}{2,}(?:-\p{Lu}\p{Ll}{2,})?\b"
)


@dataclass(frozen=True)
class AnonymizationResult:
    text: str
    findings: dict[str, int]


@dataclass(frozen=True)
class ValidatedRegexSpec:
    entity: str
    name: str
    regex: str
    score: float
    validator: str


VALIDATED_REGEX_RECOGNIZERS: tuple[ValidatedRegexSpec, ...] = (
    ValidatedRegexSpec(
        "PL_PESEL",
        "Polish PESEL with checksum",
        r"(?<!\d)\d{11}(?!\d)",
        0.95,
        "pesel",
    ),
    ValidatedRegexSpec(
        "PL_NIP",
        "Polish NIP with checksum",
        r"(?<!\d)(?:\d{3}[-\s]?\d{3}[-\s]?\d{2}[-\s]?\d{2}|\d{10})(?!\d)",
        0.92,
        "nip",
    ),
    ValidatedRegexSpec(
        "PL_REGON",
        "Polish REGON with checksum",
        r"(?<!\d)(?:\d{9}|\d{14})(?!\d)",
        0.9,
        "regon",
    ),
    ValidatedRegexSpec(
        "PL_IBAN",
        "Polish IBAN with checksum",
        r"\bPL\s?\d{2}(?:[\s-]?\d{4}){6}\b",
        0.95,
        "iban",
    ),
    ValidatedRegexSpec(
        "BANK_ACCOUNT",
        "Polish NRB account with checksum",
        r"(?<!\d)\d{2}(?:[\s-]?\d{4}){6}(?!\d)",
        0.9,
        "nrb",
    ),
    ValidatedRegexSpec(
        "PAYMENT_CARD",
        "Payment card with Luhn checksum",
        r"(?<!\d)(?:\d[ -]*?){13,19}(?!\d)",
        0.82,
        "luhn",
    ),
)


REGEX_RECOGNIZERS: tuple[RegexRecognizerSpec, ...] = (
    RegexRecognizerSpec(
        "PL_PERSON_NAME",
        "Polish first name and surname",
        POLISH_NAME_PATTERN,
        0.86,
        ("imię", "imie", "nazwisko", "osoba", "klient", "pacjent"),
    ),
    RegexRecognizerSpec(
        "PL_SURNAME",
        "Polish surname with context",
        POLISH_CONTEXT_SURNAME_PATTERN,
        0.74,
        ("nazwisko", "pan", "pani", "klient", "pacjent"),
    ),
    RegexRecognizerSpec("PL_ID_CARD", "Polish ID card", r"\b[A-Z]{3}\s?\d{6}\b", 0.78),
    RegexRecognizerSpec("PL_PASSPORT", "Polish passport", r"\b[A-Z]{2}\s?\d{7}\b", 0.65),
    RegexRecognizerSpec(
        "IBAN",
        "Generic IBAN",
        r"\b[A-Z]{2}\d{2}(?:[\s-]?[A-Z0-9]){11,30}\b",
        0.65,
        ("iban", "account", "konto"),
    ),
    RegexRecognizerSpec("SWIFT_BIC", "SWIFT/BIC", r"\b[A-Z]{6}[A-Z0-9]{2}(?:[A-Z0-9]{3})?\b", 0.55),
    RegexRecognizerSpec(
        "PL_PHONE_NUMBER",
        "Polish phone number",
        r"(?<!\d)(?:(?:\+|00)48[\s-]?)?(?:\d{3}[\s-]?){3}(?!\d)",
        0.72,
        ("tel", "telefon", "phone", "mobile"),
    ),
    RegexRecognizerSpec("PL_POSTAL_CODE", "Polish postal code", r"\b\d{2}-\d{3}\b", 0.55),
    RegexRecognizerSpec(
        "PL_BIRTH_DATE",
        "Polish birth date with context",
        r"(?i:\b(?:data\s+urodzenia|ur\.|urodzon[ay]|dob))[:\s]+"
        r"(?:\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\d{4}-\d{2}-\d{2}|"
        r"\d{1,2}\s+(?i:stycznia|lutego|marca|kwietnia|maja|czerwca|lipca|"
        r"sierpnia|września|pazdziernika|października|listopada|grudnia)\s+\d{4})\b",
        0.78,
        ("urodzenia", "urodzony", "urodzona", "dob"),
    ),
    RegexRecognizerSpec(
        "PL_BIRTH_PLACE",
        "Polish birth place with context",
        r"(?i:\b(?:miejsce\s+urodzenia|ur\.\s+w|urodzon[ay]\s+w))[:\s]+"
        r"\p{Lu}\p{Ll}{2,}(?:[\s-]+\p{Lu}\p{Ll}{2,}){0,3}\b",
        0.74,
        ("miejsce", "urodzenia", "urodzony", "urodzona"),
    ),
    RegexRecognizerSpec(
        "PL_ADDRESS",
        "Polish street address",
        r"(?i:\b(?:ul\.|ulica|al\.|aleja|pl\.|plac|os\.|osiedle))\s+"
        r"[A-ZŁŚŻŹĆŃÓĘĄ][\wąćęłńóśźżĄĆĘŁŃÓŚŹŻ.-]+"
        r"(?:\s+[A-ZŁŚŻŹĆŃÓĘĄa-ząćęłńóśźż][\wąćęłńóśźżĄĆĘŁŃÓŚŹŻ.-]+){0,4}"
        r"\s+\d+[A-Za-z]?(?:/\d+[A-Za-z]?)?",
        0.76,
    ),
    RegexRecognizerSpec(
        "PL_MEDICAL_FACILITY",
        "Polish medical facility",
        r"\b(?:Szpital|Klinika|Centrum\s+Medyczne|SPZOZ|NZOZ|Przychodnia)"
        r"(?:\s+[\p{Lu}\p{Ll}\d\"'.-]+){1,10}\b",
        0.78,
        ("szpital", "klinika", "pacjent", "oddział"),
    ),
    RegexRecognizerSpec(
        "PL_PUBLIC_OFFICE",
        "Polish public office",
        r"\b(?:Urząd|Sąd|Prokuratura|Komenda|Ministerstwo|Starostwo|"
        r"Izba\s+Administracji\s+Skarbowej|Urząd\s+Skarbowy)"
        r"(?:\s+[\p{Lu}\p{Ll}\d\"'.-]+){1,10}\b",
        0.77,
        ("urząd", "sąd", "sprawa", "decyzja"),
    ),
    RegexRecognizerSpec(
        "PL_COMPANY",
        "Polish company name",
        r"\b[\p{Lu}][\p{L}\d&'.-]+(?:\s+[\p{Lu}][\p{L}\d&'.-]+){0,6}\s+"
        r"(?:sp\.?\s*z\s*o\.?o\.?|S\.?A\.?|spółka\s+akcyjna|"
        r"fundacja|stowarzyszenie)\b",
        0.76,
        ("firma", "spółka", "pracodawca", "nip", "regon"),
    ),
    RegexRecognizerSpec(
        "PL_CASE_NUMBER",
        "Polish case number",
        r"(?i:\b(?:sygn\.?\s*akt|sygnatura|nr\s+sprawy|znak\s+sprawy))[:\s]+"
        r"[A-ZĄĆĘŁŃÓŚŹŻIVX0-9][A-ZĄĆĘŁŃÓŚŹŻ0-9 ./_-]{3,40}\b",
        0.82,
        ("sprawa", "sygnatura", "akta"),
    ),
    RegexRecognizerSpec(
        "PL_CONTRACT_NUMBER",
        "Polish contract number",
        r"(?i:\b(?:nr\s+umowy|umowa\s+nr|kontrakt\s+nr))[:\s]+"
        r"[A-ZĄĆĘŁŃÓŚŹŻ0-9][A-ZĄĆĘŁŃÓŚŹŻ0-9 ./_-]{3,40}\b",
        0.8,
        ("umowa", "kontrakt"),
    ),
    RegexRecognizerSpec(
        "PL_LICENSE_PLATE",
        "Polish license plate",
        r"\b[A-Z]{2,3}\s?[A-Z0-9]{4,5}\b",
        0.4,
        ("plate", "rejestracja", "pojazd"),
    ),
    RegexRecognizerSpec(
        "US_SSN",
        "US SSN",
        r"\b\d{3}-\d{2}-\d{4}\b",
        0.8,
        ("ssn", "social", "security"),
    ),
    RegexRecognizerSpec("UK_NINO", "UK National Insurance Number", r"\b[A-Z]{2}\d{6}[A-D]\b", 0.7),
    RegexRecognizerSpec(
        "IP_ADDRESS",
        "IPv4",
        r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}"
        r"(?:25[0-5]|2[0-4]\d|1?\d?\d)\b",
        0.72,
    ),
    RegexRecognizerSpec("IPV6_ADDRESS", "IPv6", r"\b(?:[0-9a-f]{1,4}:){2,7}[0-9a-f]{1,4}\b", 0.65),
    RegexRecognizerSpec(
        "MAC_ADDRESS",
        "MAC address",
        r"(?i:\b[0-9a-f]{2}(?::[0-9a-f]{2}){5}\b)",
        0.7,
    ),
    RegexRecognizerSpec(
        "UUID",
        "UUID",
        r"(?i:\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
        r"[89ab][0-9a-f]{3}-[0-9a-f]{12}\b)",
        0.6,
    ),
    RegexRecognizerSpec("VIN", "Vehicle VIN", r"\b[A-HJ-NPR-Z0-9]{17}\b", 0.55),
    RegexRecognizerSpec(
        "JWT",
        "JSON Web Token",
        r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b",
        0.85,
    ),
    RegexRecognizerSpec(
        "BEARER_TOKEN",
        "Bearer token",
        r"(?i:\bBearer)\s+[A-Za-z0-9._~+/=-]{20,}\b",
        0.85,
    ),
    RegexRecognizerSpec(
        "API_KEY",
        "API key",
        r"(?i:\b(?:sk|pk|api|key|token|secret))[_-]?[A-Za-z0-9]{16,}\b",
        0.65,
        ("api", "key", "token", "secret"),
    ),
)


class BlankNlpEngine(NlpEngine):
    """Lightweight Presidio NLP engine: tokenization only, no downloaded NER model."""

    def __init__(self, languages: Iterable[str] = ("en",)) -> None:
        self._models = {language: spacy.blank("en") for language in languages}
        self._loaded = True

    def load(self) -> None:
        self._loaded = True

    def is_loaded(self) -> bool:
        return self._loaded

    def process_text(self, text: str, language: str) -> NlpArtifacts:
        doc = self._models[language](text)
        lemmas = [token.lemma_ or token.text.lower() for token in doc]
        return NlpArtifacts(
            entities=[],
            tokens=doc,
            tokens_indices=[token.idx for token in doc],
            lemmas=lemmas,
            nlp_engine=self,
            language=language,
        )

    def process_batch(
        self,
        texts: Iterable[str],
        language: str,
        batch_size: int = 1,
        n_process: int = 1,
        **kwargs: Any,
    ) -> Iterator[tuple[str, NlpArtifacts]]:
        for text in texts:
            yield text, self.process_text(text, language)

    def is_stopword(self, word: str, language: str) -> bool:
        return bool(self._models[language].vocab[word].is_stop)

    def is_punct(self, word: str, language: str) -> bool:
        return self._models[language](word)[0].is_punct if word else False

    def get_supported_entities(self) -> list[str]:
        return []

    def get_supported_languages(self) -> list[str]:
        return list(self._models)


class ValidatedRegexRecognizer(EntityRecognizer):
    def __init__(self, spec: ValidatedRegexSpec) -> None:
        super().__init__(supported_entities=[spec.entity], name=spec.name, supported_language="en")
        self._spec = spec
        self._pattern = regex.compile(spec.regex, flags=regex.IGNORECASE | regex.MULTILINE)

    def analyze(
        self, text: str, entities: list[str], nlp_artifacts: NlpArtifacts | None = None
    ) -> list[RecognizerResult]:
        if self._spec.entity not in entities:
            return []

        results: list[RecognizerResult] = []
        for match in self._pattern.finditer(text):
            value = match.group(0)
            if _validate_identifier(self._spec.validator, value):
                results.append(
                    RecognizerResult(
                        entity_type=self._spec.entity,
                        start=match.start(),
                        end=match.end(),
                        score=self._spec.score,
                    )
                )
        return results


class GlinerRecognizer(EntityRecognizer):
    LABEL_TO_ENTITY = {
        "person": "PL_PERSON_NAME",
        "name": "PL_PERSON_NAME",
        "organization": "ORGANIZATION",
        "company": "ORGANIZATION",
        "hospital": "PL_MEDICAL_FACILITY",
        "medical facility": "PL_MEDICAL_FACILITY",
        "government office": "PL_PUBLIC_OFFICE",
        "public office": "PL_PUBLIC_OFFICE",
        "location": "LOCATION",
        "place": "LOCATION",
        "address": "PL_ADDRESS",
        "date of birth": "PL_BIRTH_DATE",
        "birth place": "PL_BIRTH_PLACE",
        "case number": "PL_CASE_NUMBER",
        "contract number": "PL_CONTRACT_NUMBER",
    }
    LABELS = tuple(LABEL_TO_ENTITY)

    def __init__(self, model_name: str, threshold: float, model: Any | None = None) -> None:
        super().__init__(
            supported_entities=sorted(set(self.LABEL_TO_ENTITY.values())),
            name="GLiNER multilingual NER",
            supported_language="en",
        )
        if model is None:
            try:
                from gliner import GLiNER
            except ImportError as error:
                raise RuntimeError(
                    "GLiNER enabled, but package is missing. Run `uv sync --extra ml`."
                ) from error
            model = GLiNER.from_pretrained(model_name)

        self._model = model
        self._threshold = threshold

    def analyze(
        self, text: str, entities: list[str], nlp_artifacts: NlpArtifacts | None = None
    ) -> list[RecognizerResult]:
        predictions = self._model.predict_entities(
            text,
            list(self.LABELS),
            threshold=self._threshold,
        )
        results: list[RecognizerResult] = []
        for prediction in predictions:
            label = str(prediction.get("label", "")).lower()
            entity = self.LABEL_TO_ENTITY.get(label)
            if not entity or entity not in entities:
                continue
            start = int(prediction["start"])
            end = int(prediction["end"])
            if start >= end:
                continue
            results.append(
                RecognizerResult(
                    entity_type=entity,
                    start=start,
                    end=end,
                    score=float(prediction.get("score", self._threshold)),
                )
            )
        return results


def _validate_identifier(kind: str, value: str) -> bool:
    digits = _digits(value)
    match kind:
        case "pesel":
            return _valid_pesel(digits)
        case "nip":
            return _valid_nip(digits)
        case "regon":
            return _valid_regon(digits)
        case "iban":
            return _valid_iban(value)
        case "nrb":
            return len(digits) == 26 and _valid_iban(f"PL{digits}")
        case "luhn":
            return _valid_luhn(digits)
        case _:
            return False


def _digits(value: str) -> str:
    return "".join(character for character in value if character.isdigit())


def _valid_pesel(digits: str) -> bool:
    if len(digits) != 11:
        return False
    weights = (1, 3, 7, 9, 1, 3, 7, 9, 1, 3)
    checksum = sum(int(digit) * weight for digit, weight in zip(digits[:10], weights, strict=True))
    return (10 - checksum % 10) % 10 == int(digits[-1])


def _valid_nip(digits: str) -> bool:
    if len(digits) != 10:
        return False
    weights = (6, 5, 7, 2, 3, 4, 5, 6, 7)
    checksum = (
        sum(int(digit) * weight for digit, weight in zip(digits[:9], weights, strict=True))
        % 11
    )
    return checksum != 10 and checksum == int(digits[-1])


def _valid_regon(digits: str) -> bool:
    if len(digits) == 9:
        return _valid_regon9(digits)
    if len(digits) == 14:
        weights = (2, 4, 8, 5, 0, 9, 7, 3, 6, 1, 2, 4, 8)
        checksum = (
            sum(int(digit) * weight for digit, weight in zip(digits[:13], weights, strict=True))
            % 11
        )
        return (0 if checksum == 10 else checksum) == int(digits[-1])
    return False


def _valid_regon9(digits: str) -> bool:
    weights = (8, 9, 2, 3, 4, 5, 6, 7)
    checksum = (
        sum(int(digit) * weight for digit, weight in zip(digits[:8], weights, strict=True))
        % 11
    )
    return (0 if checksum == 10 else checksum) == int(digits[-1])


def _valid_iban(value: str) -> bool:
    compact = regex.sub(r"[\s-]+", "", value).upper()
    if not regex.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", compact):
        return False
    rearranged = compact[4:] + compact[:4]
    numeric = "".join(
        str(ord(character) - 55) if character.isalpha() else character
        for character in rearranged
    )
    return int(numeric) % 97 == 1


def _valid_luhn(digits: str) -> bool:
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    parity = len(digits) % 2
    for index, character in enumerate(digits):
        value = int(character)
        if index % 2 == parity:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


class PresidioTextAnonymizer:
    def __init__(
        self,
        *,
        gliner_enabled: bool = False,
        gliner_model: str = "urchade/gliner_multi_pii-v1",
        gliner_threshold: float = 0.45,
    ) -> None:
        nlp_engine = BlankNlpEngine(("en",))
        registry = RecognizerRegistry(supported_languages=["en"])
        registry.load_predefined_recognizers(languages=["en"], nlp_engine=nlp_engine)

        for spec in VALIDATED_REGEX_RECOGNIZERS:
            registry.add_recognizer(ValidatedRegexRecognizer(spec))

        for spec in REGEX_RECOGNIZERS:
            registry.add_recognizer(
                PatternRecognizer(
                    supported_entity=spec.entity,
                    name=spec.pattern_name,
                    patterns=[Pattern(spec.pattern_name, spec.regex, spec.score)],
                    context=list(spec.context),
                    global_regex_flags=regex.MULTILINE | regex.DOTALL,
                )
            )

        if gliner_enabled:
            registry.add_recognizer(GlinerRecognizer(gliner_model, gliner_threshold))

        self._analyzer = AnalyzerEngine(
            registry=registry,
            nlp_engine=nlp_engine,
            supported_languages=["en"],
            default_score_threshold=0.25,
        )
        self._anonymizer = AnonymizerEngine()
        self._lock = Lock()

    def anonymize(self, text: str) -> AnonymizationResult:
        with self._lock:
            analyzer_results = self._analyzer.analyze(text=text, language="en")
            result = self._anonymizer.anonymize(text=text, analyzer_results=analyzer_results)

        findings = Counter(item.entity_type for item in analyzer_results)
        return AnonymizationResult(text=result.text, findings=dict(sorted(findings.items())))
