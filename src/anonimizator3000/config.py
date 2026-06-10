from dataclasses import dataclass
from os import getenv


def _get_int(name: str, default: int) -> int:
    raw = getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as error:
        raise ValueError(f"{name} must be an integer") from error
    if value <= 0:
        raise ValueError(f"{name} must be greater than 0")
    return value


def _get_bool(name: str, default: bool) -> bool:
    raw = getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    max_file_bytes: int = 5_000_000
    max_text_chars: int = 250_000
    queue_max_size: int = 20
    worker_count: int = 1
    max_active_jobs_per_ip: int = 2
    rate_limit_submissions: int = 6
    rate_limit_window_seconds: int = 600
    job_ttl_seconds: int = 900
    trust_proxy_headers: bool = False
    gliner_enabled: bool = False
    gliner_model: str = "urchade/gliner_multi_pii-v1"
    gliner_threshold: float = 0.45

    @property
    def max_multipart_body_bytes(self) -> int:
        return self.max_file_bytes + 128_000


def settings_from_env() -> Settings:
    return Settings(
        max_file_bytes=_get_int("ANON_MAX_FILE_BYTES", Settings.max_file_bytes),
        max_text_chars=_get_int("ANON_MAX_TEXT_CHARS", Settings.max_text_chars),
        queue_max_size=_get_int("ANON_QUEUE_MAX_SIZE", Settings.queue_max_size),
        worker_count=_get_int("ANON_WORKER_COUNT", Settings.worker_count),
        max_active_jobs_per_ip=_get_int(
            "ANON_MAX_ACTIVE_JOBS_PER_IP", Settings.max_active_jobs_per_ip
        ),
        rate_limit_submissions=_get_int(
            "ANON_RATE_LIMIT_SUBMISSIONS", Settings.rate_limit_submissions
        ),
        rate_limit_window_seconds=_get_int(
            "ANON_RATE_LIMIT_WINDOW_SECONDS", Settings.rate_limit_window_seconds
        ),
        job_ttl_seconds=_get_int("ANON_JOB_TTL_SECONDS", Settings.job_ttl_seconds),
        trust_proxy_headers=_get_bool("ANON_TRUST_PROXY_HEADERS", Settings.trust_proxy_headers),
        gliner_enabled=_get_bool("ANON_GLINER_ENABLED", Settings.gliner_enabled),
        gliner_model=getenv("ANON_GLINER_MODEL", Settings.gliner_model),
        gliner_threshold=float(getenv("ANON_GLINER_THRESHOLD", Settings.gliner_threshold)),
    )
