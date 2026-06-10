# Posejdon

Text anonymization package.

Scope:

- Presidio pipeline
- Polish regex recognizers
- checksum validators for identifiers
- optional GLiNER recognizer

No document parsing. No web server. No queue.

## Usage

```python
from posejdon import TextAnonymizer

anonymizer = TextAnonymizer()
result = anonymizer.anonymize("Jan Kowalski PESEL 44051401359")

print(result.text)
print(result.findings)
```

## Optional GLiNER

```bash
uv sync --extra ml
```

```python
from posejdon import TextAnonymizer

anonymizer = TextAnonymizer(gliner_enabled=True)
```
