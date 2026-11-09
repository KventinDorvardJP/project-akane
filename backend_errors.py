# Shared LLM failure type so conversation handling can stay independent of a provider.
class BackendError(Exception):
    pass