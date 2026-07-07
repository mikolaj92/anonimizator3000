"""The Carrier flow that wires the anonymization blocks into a dependency graph.

convert -> load -> anonymize -> serialize, each a ``python_function`` step
resolved by ``module:function`` reference. Fala owns the ordering, retries, and
status; the blocks in :mod:`anonimizator3000.steps` own the work.
"""

from __future__ import annotations

from fala.models import CarrierAdapterSpec, CarrierFlowSpec, CarrierFlowStepSpec

FLOW_ID = "document_anonymization"
CAPABILITY = "documents.anonymize"

CONVERT_STEP = "convert"
LOAD_STEP = "load"
ANONYMIZE_STEP = "anonymize"
SERIALIZE_STEP = "serialize"

_STEP_TIMEOUT_SECONDS = 300


def _step(step_id: str, title: str, function: str, needs: list[str]) -> CarrierFlowStepSpec:
    return CarrierFlowStepSpec(
        id=step_id,
        title=title,
        capability=CAPABILITY,
        adapter=CarrierAdapterSpec(
            kind="python_function",
            ref=f"anonimizator3000.steps:{function}",
        ),
        needs=needs,
        timeout_seconds=_STEP_TIMEOUT_SECONDS,
    )


DOCUMENT_FLOW = CarrierFlowSpec(
    id=FLOW_ID,
    title="Anonimizacja dokumentu",
    steps=[
        _step(CONVERT_STEP, "Normalizacja do DOCX", "convert", []),
        _step(LOAD_STEP, "Ekstrakcja tekstu", "load", [CONVERT_STEP]),
        _step(ANONYMIZE_STEP, "Anonimizacja", "anonymize", [LOAD_STEP]),
        _step(SERIALIZE_STEP, "Zapis DOCX", "serialize", [ANONYMIZE_STEP]),
    ],
)
