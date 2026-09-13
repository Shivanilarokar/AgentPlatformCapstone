"""Two numbers every agent carries, and why each one is what it is.

    quality  0-100   "does it work?"      from its runs
    safety   A-D     "is it set up safely?" from its configuration and registry

Rule 6: "you must be able to point at exactly why a number is what it is - no
asking a model to guess a score. Then make them matter: an agent that scores
badly cannot be published."

So every point comes from a named check with a one-line reason, the checks are
returned alongside the numbers, and `can_publish` / `blocked_by` are derived
from them - the Publish button reads those, nothing else.
"""

from app.scoring.score import PUBLISH_MIN_GRADE, PUBLISH_MIN_QUALITY, Score, score_agent

__all__ = ["PUBLISH_MIN_GRADE", "PUBLISH_MIN_QUALITY", "Score", "score_agent"]
