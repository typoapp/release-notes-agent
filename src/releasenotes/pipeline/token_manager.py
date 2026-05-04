from ..llm.base import BaseLLMProvider
from ..schemas.change_group import ChangeGroup

TOKENS_PER_GROUP_ESTIMATE = 300
OUTPUT_TOKENS_PER_GROUP_ESTIMATE = 350


def plan_calls(
    groups: list[ChangeGroup],
    provider: BaseLLMProvider,
    output_budget: int = 4096,
) -> list[list[ChangeGroup]]:
    """
    Returns a list of chunks. If all groups fit in one call, returns [[all_groups]].
    If not, chunks into batches of max 20 groups.
    """
    usable = provider.context_window - output_budget - 2000
    estimated = len(groups) * TOKENS_PER_GROUP_ESTIMATE
    output_capacity = max(1, output_budget // OUTPUT_TOKENS_PER_GROUP_ESTIMATE)
    if estimated <= usable and len(groups) <= output_capacity:
        return [groups]
    chunk_size = max(1, min(20, usable // TOKENS_PER_GROUP_ESTIMATE, output_capacity))
    return [groups[i:i + chunk_size] for i in range(0, len(groups), chunk_size)]
