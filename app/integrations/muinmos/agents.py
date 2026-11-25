from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class AgentDefinition:
    key: str
    name: str
    purpose: str
    stage: str
    system_of_record: str
    dependencies: tuple[str, ...] = ()


AGENTS: tuple[AgentDefinition, ...] = (
    AgentDefinition("orchestration", "Orchestration agent", "Coordinate the end-to-end onboarding journey, dependencies, hand-offs and case state.", "workflow", "Everpay"),
    AgentDefinition("screening", "Screening agent", "Screen customers, beneficial owners and counterparties against applicable sanctions, PEP and adverse-media sources.", "risk", "Muinmos / configured screening providers", ("identity_verification",)),
    AgentDefinition("identity_verification", "Identity verification agent", "Verify an individual’s identity and liveness, and reconcile verified attributes.", "identity", "Muinmos / AiPrise"),
    AgentDefinition("proof_of_residence", "Proof of residence agent", "Collect and validate proof-of-address evidence and residence attributes.", "identity", "Muinmos / configured verification providers", ("document_collection",)),
    AgentDefinition("kyb", "KYB agent", "Verify legal entities, ownership, officers, beneficial owners and operating legitimacy.", "business", "Muinmos / AiPrise"),
    AgentDefinition("client_classification", "Client classification agent", "Classify the client under the applicable jurisdictional and business relationship rules.", "classification", "Muinmos"),
    AgentDefinition("suitability_appropriateness", "Suitability & appropriateness agent", "Assess suitability and appropriateness where the product, service and regulatory perimeter require it.", "classification", "Muinmos", ("client_classification",)),
    AgentDefinition("document_collection", "Document collection agent", "Request, receive, validate and track required onboarding evidence.", "documents", "Muinmos / Everpay document service"),
    AgentDefinition("client_risk_assessment", "Client risk assessment agent", "Aggregate customer, ownership, jurisdiction, product, channel and screening evidence into a risk assessment.", "risk", "Muinmos / Everpay policy engine", ("screening", "kyb")),
    AgentDefinition("perpetual_kyc_monitoring", "Perpetual KYC monitoring agent", "Reassess customers and businesses on events and scheduled review cycles; create review tasks when evidence changes.", "monitoring", "Muinmos / AiPrise / configured screening providers", ("client_risk_assessment",)),
)


def agent_catalog() -> list[dict]:
    return [asdict(agent) for agent in AGENTS]
