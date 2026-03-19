# ADR-025: AIProvider Protocol Abstraction

## Status

Accepted

## Date

2026-03-17

## Context

APME's remediation engine needs LLM integration for Tier 2 (AI-proposable) violations. The initial implementation uses Abbenay as the AI backend via its `abbenay_client` Python library. However, the engine should not be tightly coupled to any specific LLM provider or client library because:

1. **Testability.** Unit tests for the remediation engine should not require a running Abbenay daemon or network access. A mock provider must be trivially substitutable.

2. **Provider flexibility.** While Abbenay abstracts multiple LLM providers behind a single gRPC interface, some deployments may want to call an LLM SDK directly (OpenAI, Anthropic) without running a daemon, or use a different abstraction layer entirely.

3. **Optional dependency.** The `abbenay_client` package is an optional install (`pip install apme-engine[ai]`). The core engine must function without it. Importing `abbenay_client` at module level would make it a hard dependency.

4. **Single coupling point.** If the Abbenay client API changes (method signatures, async behavior, response format), the blast radius should be exactly one file, not spread across the engine and CLI.

## Decision

**Define `AIProvider` as a Python `typing.Protocol` with a single async method `propose_fix()`. The engine depends only on this protocol. Concrete implementations live in separate modules.**

```python
class AIProvider(Protocol):
    async def propose_fix(
        self,
        violation: ViolationDict,
        file_content: str,
        *,
        model: str | None = None,
        feedback: str | None = None,
    ) -> AIProposal | None: ...
```

The default implementation is `AbbenayProvider` in `src/apme_engine/remediation/abbenay_provider.py` -- the only file in the codebase that imports `abbenay_client`.

The engine accepts `ai_provider: AIProvider | None = None` in its constructor. When `None`, AI escalation is skipped entirely (Tier 2 violations remain as "AI-candidate" in the report).

## Options Considered

### Option A: Direct `abbenay_client` import in engine (rejected)

```python
from abbenay_client import AbbenayClient

class RemediationEngine:
    def __init__(self, ..., abbenay_host="localhost", abbenay_port=50057):
        self._client = AbbenayClient(host=abbenay_host, port=abbenay_port)
```

**Rejected.** Hard couples the engine to `abbenay_client`. Cannot test without mocking at the import level. Makes `abbenay_client` a de-facto hard dependency. Changing the client API requires modifying engine internals.

### Option B: ABC base class (rejected)

```python
from abc import ABC, abstractmethod

class AIProvider(ABC):
    @abstractmethod
    async def propose_fix(self, ...) -> AIProposal | None: ...
```

**Rejected.** Requires concrete implementations to inherit from the base class. A `Protocol` is structurally typed -- any object with a matching `propose_fix` method satisfies the contract, including simple lambdas and mock objects in tests. This is more Pythonic and lighter weight.

### Option C: Configuration-driven factory (rejected)

```python
AI_PROVIDERS = {
    "abbenay": AbbenayProvider,
    "openai": DirectOpenAIProvider,
    "mock": MockProvider,
}
provider = AI_PROVIDERS[config["ai_provider"]]()
```

**Rejected.** Over-engineered for the current requirement (one real provider). Adds a registry/factory layer that provides no benefit until there are multiple production providers. The Protocol approach supports future providers without any factory -- just write a class that satisfies the protocol.

## Consequences

### Positive

- **One coupling point.** Only `abbenay_provider.py` imports `abbenay_client`. The rest of the codebase is provider-agnostic.
- **Testable.** Tests use a `MockAIProvider` that returns canned `AIProposal` objects. No network, no daemon, no mocking library gymnastics.
- **Optional dependency.** `abbenay_client` is only imported inside `AbbenayProvider.__init__()`, wrapped in a try/except with a clear error message. The core package installs and runs without it.
- **Future-proof.** Adding a `DirectOpenAIProvider` or `OllamaProvider` requires writing one file that satisfies the protocol. No engine changes, no factory registration.

### Negative

- **Indirection.** One extra layer between the engine and the LLM client. Acceptable given the benefits; the protocol is a single method.
- **Async boundary.** The protocol method is `async`. The engine currently runs synchronously. The boundary is bridged with `asyncio.run()` at the engine level.

## Implementation Notes

- `AIProvider` protocol and `AIProposal` dataclass: `src/apme_engine/remediation/ai_provider.py`
- `AbbenayProvider`: `src/apme_engine/remediation/abbenay_provider.py`
- Engine wiring: `RemediationEngine.__init__(ai_provider: AIProvider | None = None)`
- CLI creates `AbbenayProvider` only when `--ai` is set, after preflight health check
- Full design: `docs/DESIGN_AI_ESCALATION.md`

## Related Decisions

- [ADR-009](ADR-009-remediation-engine.md): Established the three-tier remediation model and separation of remediation from scanning
- [ADR-023](ADR-023-per-finding-classification.md): Per-finding `RemediationResolution` enum includes AI-specific states (AI_PROPOSED, AI_FAILED, AI_LOW_CONFIDENCE, USER_REJECTED) that this protocol populates
