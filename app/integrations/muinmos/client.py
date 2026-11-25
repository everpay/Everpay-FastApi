class MuinmosContractUnavailable(RuntimeError):
    """Raised until Muinmos supplies its production API and webhook contract."""


class MuinmosClient:
    """
    Deliberately does not guess Muinmos endpoint paths, authentication, request
    schemas, or callback signatures. Public product pages describe capabilities,
    not an implementable API contract.
    """

    async def execute_agent(self, *_args, **_kwargs):
        raise MuinmosContractUnavailable(
            "Muinmos live execution is disabled until its API base URL, authentication, "
            "agent endpoints, request/response schemas, and webhook signing contract are supplied."
        )
