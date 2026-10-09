from fastapi import APIRouter, Depends
from app.schemas import SimulationRequest, SimulationResult
from app.services.simulator import simulate_financial_decision
from app.services.audit import record_audit_log
from app.security import get_current_role

router = APIRouter(prefix="/api/simulate", tags=["What-If Simulation"])

@router.post("", response_model=SimulationResult)
def run_simulation(
    req: SimulationRequest,
    role: str = Depends(get_current_role)
):
    """
    Simulates the impact of a financial decision on savings goals and cash flow.
    """
    user_id = 1
    result = simulate_financial_decision(
        scenario_type=req.scenario_type,
        amount=req.amount,
        description=req.description,
        frequency=req.frequency,
        goal_id=req.goal_id,
        user_id=user_id
    )

    record_audit_log(
        user_role=role,
        action_type="SIMULATION_RUN",
        resource="SIMULATOR",
        status="SUCCESS",
        details=f"Ran {req.scenario_type} simulation of {req.amount:,.0f} ₫. Impact: {result['impact_level']} (Delta: {result['delay_or_advance_months']} mo)"
    )

    return result
