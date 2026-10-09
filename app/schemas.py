from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Dict, Any, Literal
import re

# --- User & Profile ---
class UserBase(BaseModel):
    username: str
    full_name: str
    role: str = "User"
    monthly_income: float = Field(default=0, ge=0)

class ProfileUpdate(BaseModel):
    full_name: Optional[str] = None
    monthly_income: float = Field(default=0, ge=0)
    est_monthly_expense: float = Field(default=0, ge=0)
    risk_profile: Literal['conservative', 'moderate', 'aggressive'] = 'moderate'
    inflation: float = Field(default=0.03, ge=0.0, le=0.20)
    emergency_months: int = Field(default=6, ge=1, le=24)
    expected_return: Optional[float] = None
    volatility: Optional[float] = None
    currency: Optional[str] = 'USD'

class RoleSwitchRequest(BaseModel):
    role: str = Field(..., description="Target role: Viewer, User, or Admin")

# --- Categories ---
class CategoryCreate(BaseModel):
    name: str = Field(..., min_length=1)
    type: Literal['income', 'expense']
    is_essential: bool = True

# --- Transactions ---
class TransactionCreate(BaseModel):
    category: str
    amount: float = Field(..., gt=0, description="Transaction amount in USD")
    type: Literal['income', 'expense']
    description: str
    date: str = Field(..., description="YYYY-MM-DD")
    is_essential: Optional[bool] = True

    @field_validator('date')
    @classmethod
    def validate_date_format(cls, v: str) -> str:
        if not re.match(r'^\d{4}-\d{2}-\d{2}$', v):
            raise ValueError('Date must be in YYYY-MM-DD format')
        return v

class TransactionResponse(BaseModel):
    id: int
    user_id: int
    category: str
    amount: float
    type: str
    description: str
    date: str
    is_essential: bool
    is_anomaly: bool
    anomaly_reason: Optional[str] = None
    created_at: Optional[str] = None

# --- Anomaly ---
class AnomalyItem(BaseModel):
    id: int
    date: str
    category: str
    amount: float
    description: str
    z_score: float
    moving_average: float
    std_dev: float
    severity: str
    reason: str

class AnomalyScanResponse(BaseModel):
    total_scanned: int
    anomalies_detected: int
    anomalies: List[AnomalyItem]
    scan_timestamp: str

# --- Goals & Planner ---
class GoalCreate(BaseModel):
    title: str
    target_amount: float = Field(..., gt=0, description="Target savings in USD")
    current_savings: float = Field(default=0, ge=0)
    monthly_contribution: float = Field(default=0, ge=0)
    target_date: str = Field(..., description="Target completion date YYYY-MM-DD")
    notes: Optional[str] = ""

    @field_validator('target_date')
    @classmethod
    def validate_target_date(cls, v: str) -> str:
        if not re.match(r'^\d{4}-\d{2}-\d{2}$', v):
            raise ValueError('Target date must be in YYYY-MM-DD format')
        return v

class CategoryAdjustment(BaseModel):
    category: str
    current_spending: float
    cut_percentage: float
    recommended_spending: float
    monthly_saved: float
    rationale: str

class PlanDetail(BaseModel):
    plan_name: str
    monthly_savings_target: float
    months_to_complete: float
    feasibility: str
    monthly_gap: float
    surplus_or_deficit: str
    adjustments: List[CategoryAdjustment]
    total_spending_cut: float
    ai_recommendation: str

class GoalPlanResponse(BaseModel):
    goal_id: Optional[int] = None
    title: str
    target_amount: float
    current_savings: float
    target_date: str
    months_remaining: int
    monthly_income: float
    monthly_expense: float
    current_monthly_surplus: float
    required_monthly_savings: float
    plans: List[PlanDetail]
    summary_advice: str
    needs_setup: Optional[bool] = False
    low_data: Optional[bool] = False

# --- What-If Simulation ---
class SimulationRequest(BaseModel):
    goal_id: Optional[int] = None
    scenario_type: str = Field(..., description="'one_off_purchase', 'recurring_expense_change', 'income_change'")
    amount: float = Field(..., description="Amount in USD (must not be 0)")
    frequency: str = Field(default="one_time", description="'one_time' or 'monthly'")
    description: str = Field(default="Simulated Financial Event")
    target_category: Optional[str] = "Discretionary"

    @field_validator('amount')
    @classmethod
    def validate_amount_nonzero(cls, v: float) -> float:
        if v == 0:
            raise ValueError('Amount cannot be zero')
        return v

class SimulationResult(BaseModel):
    scenario_type: str
    amount: float
    description: str
    original_savings_rate: float
    new_savings_rate: float
    original_monthly_surplus: float
    new_monthly_surplus: float
    original_completion_months: Optional[float]
    new_completion_months: Optional[float]
    delay_or_advance_months: Optional[float]
    impact_level: str
    recommendation: str
    needs_setup: Optional[bool] = False
    low_data: Optional[bool] = False

# --- Quant Models ---
class LoanVsCashRequest(BaseModel):
    price: float = Field(..., ge=0, description="Purchase price")
    down_payment: float = Field(default=0, ge=0, description="Down payment")
    annual_rate: float = Field(default=0.06, ge=0, le=1.0, description="Loan interest rate")
    months: int = Field(default=36, ge=1, le=600, description="Loan term in months")
    opportunity_return: Optional[float] = Field(default=None, ge=0, le=1.0, description="Opportunity return")

class LoanVsCashResponse(BaseModel):
    monthly_payment: float
    total_interest: float
    npv_cost_loan: float
    npv_cost_cash: float
    better: Literal['loan', 'cash']
    needs_setup: Optional[bool] = False
    low_data: Optional[bool] = False

class MonteCarloGoalResponse(BaseModel):
    goal_id: int
    title: str
    current_savings: float
    nominal_target: float
    real_target: float
    months_remaining: int
    expected_return: float
    volatility: float
    inflation: float
    prob_success: float
    p10: float
    p50: float
    p90: float
    required_monthly_pmt: float
    paths_percentiles: List[Dict[str, Any]]
    needs_setup: Optional[bool] = False
    low_data: Optional[bool] = False

class ForecastResponse(BaseModel):
    ewma_monthly_expense_forecast: float
    forecast_lower95: float
    forecast_upper95: float
    cash_flow_at_risk_95: float
    emergency_fund_needed: float
    months_covered: int
    historical_months_used: int
    needs_setup: Optional[bool] = False
    low_data: Optional[bool] = False

# --- Progress Tracking ---
class ProgressStatus(BaseModel):
    goal_id: int
    title: str
    target_amount: float
    current_savings: float
    completion_percentage: float
    target_date: str
    months_elapsed: int
    months_remaining: int
    expected_savings_to_date: float
    actual_vs_expected_gap: float
    on_track: bool
    status_label: str
    catchup_monthly_needed: float
    actionable_advice: str

# --- Sandbox Approval Hub ---
class SandboxProposalCreate(BaseModel):
    title: str
    action_type: str
    description: str
    impact_summary: str
    projected_savings_delta: float
    payload: Dict[str, Any]

class SandboxApprovalAction(BaseModel):
    action: Literal['APPROVE', 'REJECT']
    feedback: Optional[str] = None

# --- Audit Logs ---
class AuditLogResponse(BaseModel):
    id: int
    timestamp: str
    user_role: str
    action_type: str
    resource: str
    status: str
    details: str
    ip_address: str
