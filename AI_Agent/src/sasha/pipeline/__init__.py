"""Pipeline stages. Each module defines one `typing.Protocol`; implementations swap by config.

Phase 0 implements only `llm`. The rest are interfaces for later phases.
"""
