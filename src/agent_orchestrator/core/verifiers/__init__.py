"""Bundled workspace verifiers used by `core.verification_gate.VerificationGate`.

Each verifier follows the `WorkspaceVerifier` protocol: implements an async
`verify(workdir: Path) -> Sequence[VerifierFailure]` and exposes a `name` and
a `cost_estimate_s` for the gate's cost-first ordering.

Importing this package is cheap — verifiers do all expensive work lazily
inside `verify()`.
"""

from agent_orchestrator.core.verifiers.coherence import WorkspaceCoherenceVerifier
from agent_orchestrator.core.verifiers.dependency import DependencyVerifier
from agent_orchestrator.core.verifiers.e2e_smoke import E2ESmokeVerifier
from agent_orchestrator.core.verifiers.encoding import EncodingVerifier
from agent_orchestrator.core.verifiers.entrypoint import EntrypointVerifier
from agent_orchestrator.core.verifiers.imports import ImportVerifier
from agent_orchestrator.core.verifiers.runtime_smoke import RuntimeSmokeVerifier
from agent_orchestrator.core.verifiers.syntax import SyntaxVerifier

__all__ = [
    "SyntaxVerifier",
    "DependencyVerifier",
    "EncodingVerifier",
    "ImportVerifier",
    "WorkspaceCoherenceVerifier",
    "RuntimeSmokeVerifier",
    "EntrypointVerifier",
    "E2ESmokeVerifier",
]
