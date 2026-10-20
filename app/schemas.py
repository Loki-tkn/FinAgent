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
    full_name: Optional[str] = Field(default=None, min_length=1)
    monthly_income: Optional[float] = Field(default=None, ge=0)
    est_monthly_expense: Optional[float] = Field(default=None, ge=0)
    risk_profile: Optional[Literal['conservative', 'moderate', 'aggressive']] = None
    inflation: Optional[float] = Field(default=None, ge=0.0, le=0.20)
    emergency_months: Optional[int] = Field(default=None, ge=1, le=24)
    expected_return: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    volatility: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    currency: Optional[Literal['USD']] = None

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
    source: Optional[str] = "manual"
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
    eligible_categories: int = 0
    skipped_categories: List[Dict[str, Any]] = []
    min_samples: int = 5
    message: str = ""

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
        from datetime import date as dt_date
        try:
            d = dt_date.fromisoformat(v)
            if d <= dt_date.today():
                raise ValueError('Target date must be in the future')
        except ValueError as e:
            if 'future' in str(e):
                raise
            raise ValueError('Target date must be in YYYY-MM-DD format')
        return v

class SelectPlanRequest(BaseModel):
    plan_name: Literal['Conservative', 'Balanced', 'Aggressive']
    monthly_contribution: float = Field(..., ge=0)

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
    months_to_complete: Optional[float] = None
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
    prob_success_before: Optional[float] = None
    prob_success_after: Optional[float] = None
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

# --- Monthly History ---
class HistoryItem(BaseModel):
    category: str
    type: Literal['income', 'expense']
    amount: float = Field(..., ge=0)

class HistoryMonth(BaseModel):
    month: str = Field(..., description="YYYY-MM")
    items: List[HistoryItem]

    @field_validator('month')
    @classmethod
    def validate_month(cls, v: str) -> str:
        import re
        if not re.match(r'^\d{4}-\d{2}$', v):
            raise ValueError('Month must be YYYY-MM')
        m = int(v.split('-')[1])
        if not (1 <= m <= 12):
            raise ValueError('Month must be between 01 and 12')
        return v

class HistoryImportRequest(BaseModel):
    months: List[HistoryMonth] = Field(..., max_length=24)

class HistoryImportResponse(BaseModel):
    months_saved: int
    rows_inserted: int

# --- Transaction Update ---
class TransactionUpdate(BaseModel):
    category: Optional[str] = None
    amount: Optional[float] = Field(default=None, gt=0)
    type: Optional[Literal['income', 'expense']] = None
    description: Optional[str] = None
    date: Optional[str] = None

    @field_validator('date')
    @classmethod
    def validate_date_format(cls, v: str) -> str:
        if v is None:
            return v
        from datetime import date
        try:
            date.fromisoformat(v)
        except Exception:
            raise ValueError('Date must be a valid calendar date in YYYY-MM-DD format')
        return v

# --- Goal Updates ---
class GoalUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1)
    target_amount: Optional[float] = Field(default=None, gt=0)
    target_date: Optional[str] = None
    notes: Optional[str] = None
    monthly_contribution: Optional[float] = Field(default=None, ge=0)
    status: Optional[Literal['In Progress', 'Paused', 'Completed']] = None

    @field_validator('target_date')
    @classmethod
    def validate_target_date(cls, v: str) -> str:
        if v is None:
            return v
        from datetime import date as dt_date
        try:
            d = dt_date.fromisoformat(v)
            if d <= dt_date.today():
                raise ValueError('Target date must be in the future')
        except ValueError as e:
            if 'future' in str(e):
                raise
            raise ValueError('Target date must be in YYYY-MM-DD format')
        return v

class GoalDepositRequest(BaseModel):
    amount: float = Field(..., description="Positive = deposit, negative = withdrawal")
    note: Optional[str] = None

    @field_validator('amount')
    @classmethod
    def validate_nonzero(cls, v: float) -> float:
        if v == 0:
            raise ValueError('Amount cannot be zero')
        return v

# --- Category Update ---
class CategoryUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1)
    is_essential: Optional[bool] = None

# --- Budgets ---
class BudgetUpsert(BaseModel):
    category: str
    month_year: str = Field(..., description="YYYY-MM")
    budget_limit: float = Field(..., ge=0)

    @field_validator('month_year')
    @classmethod
    def validate_month_year(cls, v: str) -> str:
        parts = v.strip().split('-')
        if len(parts) != 2 or len(parts[0]) != 4 or not parts[0].isdigit() or not parts[1].isdigit() or not (1 <= int(parts[1]) <= 12):
            raise ValueError('month_year must be in valid YYYY-MM format')
        return v.strip()

# --- Recurring Rules ---
class RecurringRuleCreate(BaseModel):
    type: Literal['income', 'expense']
    category: str
    amount: float = Field(..., gt=0)
    description: str
    day_of_month: int = Field(default=1, ge=1, le=28)

class RecurringRuleUpdate(BaseModel):
    amount: Optional[float] = Field(default=None, gt=0)
    description: Optional[str] = None
    day_of_month: Optional[int] = Field(default=None, ge=1, le=28)
    active: Optional[bool] = None

# --- CSV Import ---
class CSVImportResponse(BaseModel):
    total: int
    valid: int
    duplicates: int
    errors: List[Dict[str, Any]]
    preview: List[Dict[str, Any]]
    imported: int

