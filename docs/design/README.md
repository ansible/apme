# Design Documents

This directory contains **design rationale** documents — the reasoning behind
how specific APME subsystems were built.

> **design/** vs **architecture/**: Design docs explain **why** a subsystem
> was built the way it was — alternatives considered, trade-offs, and
> rationale. Architecture docs (in `../architecture/`) describe **how** the
> system works at runtime — what happens, in what order, and how data flows.

## Contents

| Document | Description |
|----------|-------------|
| [DESIGN_REMEDIATION.md](DESIGN_REMEDIATION.md) | Transform registry, convergence loop, AI escalation strategy |
| [DESIGN_AI_ESCALATION.md](DESIGN_AI_ESCALATION.md) | Abbenay provider, hybrid validation loop, prompt engineering |
| [DESIGN_VALIDATORS.md](DESIGN_VALIDATORS.md) | Validator abstraction, unified contract, fan-out model |
| [THIRD_PARTY_EXTENSIBILITY_OPTIONS.md](THIRD_PARTY_EXTENSIBILITY_OPTIONS.md) | Third-party extensibility options vs current CLI/Engine/remediation use (ADR-042 context) |

## When to Add a Document Here

Add a design doc when a subsystem requires explanation beyond what the
architecture pipeline docs cover — especially design alternatives that were
rejected, constraints that shaped the design, or rationale that would
otherwise be lost. For one-off architectural decisions, use an
[ADR](../../.sdlc/adrs/) instead.
