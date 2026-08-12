from typing import get_type_hints

from fala.adapters import StepRunRequest

import anonimizator3000.steps as steps
from anonimizator3000.flow import DOCUMENT_FLOW


def test_document_analysis_is_a_fala_orchestrated_chain() -> None:
    expected = [
        ("convert", [], steps.convert),
        ("load", ["convert"], steps.load),
        ("anonymize", ["load"], steps.anonymize),
        ("serialize", ["anonymize"], steps.serialize),
        ("redact_authors", ["serialize"], steps.redact_authors),
    ]

    assert len(DOCUMENT_FLOW.steps) == len(expected)
    for step, (step_id, needs, function) in zip(DOCUMENT_FLOW.steps, expected, strict=True):
        assert step.id == step_id
        assert step.needs == needs
        assert step.adapter.kind == "python_function"
        assert step.adapter.ref == f"anonimizator3000.steps:{function.__name__}"
        annotations = get_type_hints(function)
        assert annotations["request"] is StepRunRequest
        assert annotations["return"].__name__.endswith("Output")
