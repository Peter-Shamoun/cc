"""Multiple SQL candidates, clustered by executed result, then selected.

The public entry point for the bench harness is ``agent.MultiCandidateAgent``.
"""

from analyst.variants.multi_candidate.agent import MultiCandidateAgent

__all__ = ["MultiCandidateAgent"]
