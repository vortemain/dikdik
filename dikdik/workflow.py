"""Conservative intent hints and detection of premature command handoffs.

These guide work, never execute prose or grant tool permissions.
"""
import re


def explanatory(objective):
    return bool(re.match(r"\s*(?:explain\b|what\b|why\b|where\b|how\s+(?:do|does|can|to)\b)", objective, re.I))


def verification_request(objective):
    # "build" alone is usually a creation verb ("Build the API"), not a verification
    # request; "run the build" is still caught by "run". Forcing run_tests on a plain
    # reply must not fire when nothing was actually requested to be verified.
    return not explanatory(objective) and bool(re.search(
        r"\b(?:test|verify|validate|run|debug|exercise|check\s+(?:that|whether|if|the|my|this))\b", objective, re.I))


def work_request(objective):
    return not explanatory(objective) and bool(re.search(r"\b(?:fix|implement|create|add|change|update|remove|repair)\b", objective, re.I))


def visual_request(objective):
    return not explanatory(objective) and bool(re.search(
        r"\b(?:design|layout|responsive|navbar|styling|css|visual|ui|ux|webpage|website|frontend|landing\s+page)\b", objective, re.I))


def interaction_request(objective):
    return not explanatory(objective) and bool(re.search(
        r"\b(?:click|submit|interact(?:ion|ions)?|test\s+(?:the\s+)?(?:form|button|login|signup)|(?:login|signup|checkout)\s+(?:flow|works?|working))\b", objective, re.I))


def command_handoff(text):
    return bool(re.search(
        r"(?:\b(?:you\s+(?:can|should|need\s+to|must)|please|next[, :]*)\s+(?:now\s+)?(?:run|execute|start|install)\b"
        r"|(?:^|\n)\s*(?:[-*]\s*)?(?:\d+[.)]\s*)?(?:run|execute|start|install)\s+(?:[`]|(?:npm|pnpm|yarn|bun|python|pytest|cargo|go|make|docker)\b))", text, re.I))


def approval_question(text):
    return bool(re.search(r"(?:\b(?:may|can|should|shall)\s+I\s+(?:proceed|continue|run|execute|start|install|edit|fix|test)\b|\bwould\s+you\s+like\s+me\s+to\b|\bdo\s+you\s+want\s+me\s+to\b)", text, re.I))
