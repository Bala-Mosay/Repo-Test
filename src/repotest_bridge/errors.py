"""Typed exceptions surfaced by the CLI as actionable, non-zero-exit errors."""

from __future__ import annotations


class RepotestError(Exception):
    """Base class for every error this package raises deliberately."""

    exit_code = 1
    hint: str = ""

    def __str__(self) -> str:
        base = super().__str__()
        if self.hint:
            return f"{base}\n  hint: {self.hint}"
        return base


class ConfigError(RepotestError):
    """Invalid or missing configuration."""

    exit_code = 2
    hint = "Check configs/default.yaml, any --config file, and REPB_* environment variables."


class ManifestError(RepotestError):
    """A task manifest failed validation."""

    exit_code = 3
    hint = "Run `repotest-bridge validate <file.diff>` or `repotest-bridge doctor` for details."


class RepositoryError(RepotestError):
    """A repository could not be resolved or inspected."""

    exit_code = 4
    hint = "Confirm the path exists and that `base_commit` resolves in that checkout."


class ProviderError(RepotestError):
    """The model provider could not be reached or returned an unusable response."""

    exit_code = 5
    hint = "Run `repotest-bridge doctor` to check OPENAI_API_KEY / OPENAI_BASE_URL / OPENAI_MODEL."


class CredentialsError(ProviderError):
    """No usable credential is configured."""

    hint = (
        "Export OPENAI_API_KEY (and OPENAI_BASE_URL for Groq/OpenRouter), or use "
        "--provider mock for offline runs."
    )


class ParseError(RepotestError):
    """Model output could not be parsed into a structured test patch."""

    exit_code = 6
    hint = "Inspect the raw response under the run's logs/ directory."


class PatchValidationError(RepotestError):
    """A parsed patch failed a safety or structural check."""

    exit_code = 7
    hint = "Only relative, in-repository, test-tree paths are accepted."


class ExecutionError(RepotestError):
    """A bounded subprocess failed in a way that is not a measured outcome."""

    exit_code = 8
    hint = "Inspect the captured log; timeouts and output caps are reported as infra_fail."
