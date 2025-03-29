# No NLU simulation needed anymore.
# Keep other utilities if you add them.

# Example utility that might still be useful if LLM sometimes includes "فرع"
def normalize_branch_name_strict(name_from_llm):
    """
    Takes branch name from LLM and ensures it exactly matches a known branch.
    Removes common prefixes like 'فرع ' for matching.
    Returns the matched known branch name or None if no match.
    """
    from config import KNOWN_BRANCHES # Import locally to avoid circular dependency issues

    if not isinstance(name_from_llm, str):
        return None

    cleaned_name = name_from_llm.replace("فرع ", "").strip()

    # Exact match first
    if cleaned_name in KNOWN_BRANCHES:
        return cleaned_name

    # Consider case-insensitive or fuzzy matching if needed, but exact is safer here
    # for branch_known in KNOWN_BRANCHES:
    #     if cleaned_name.lower() == branch_known.lower():
    #         return branch_known

    return None # No known branch matched