/**
 * AI Personal Financial Agent - Frontend Application Logic (USD + User-Driven Setup)
 */

let currentRole = 'User';
let allCategories = [];
let activeGoalList = [];
let currentGeneratedPlans = null;
let currentProfile = null;

function formatUSD(num) {
    if (num === null || num === undefined) return '$0';
    return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }).format(num);
}

function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    
    let icon = 'info';
    if (type === 'success') icon = 'check-circle';
    if (type === 'error') icon = 'alert-circle';
    if (type === 'warning') icon = 'alert-triangle';

    toast.innerHTML = `<i data-lucide="${icon}"></i> <span>${message}</span>`;
    container.appendChild(toast);
    lucide.createIcons();

    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transform = 'translateX(20px)';
        setTimeout(() => toast.remove(), 300);
    }, 3500);
}

async function apiFetch(url, options = {}) {
    options.headers = options.headers || {};
    options.headers['X-User-Role'] = currentRole;
    options.headers['Content-Type'] = options.headers['Content-Type'] || 'application/json';

    try {
        const res = await fetch(url, options);
        if (!res.ok) {
            const errData = await res.json().catch(() => ({}));
            const msg = errData.detail || errData.error || `Server error (${res.status})`;
            showToast(msg, 'error');
            throw new Error(msg);
        }
        const data = await res.json();
        // Auto-refresh on mutating calls except excluded endpoints
        const mutating = ['POST','PUT','DELETE','PATCH'].includes((options.method || 'GET').toUpperCase());
        const exclude = [/^\/api\/simulate/, /^\/api\/goals\/calculate-plans/, /^\/api\/quant\//, /^\/api\/transactions\/scan-anomalies/];
        if (mutating && url.startsWith('/api/') && !exclude.some(r=>r.test(url))) {
            scheduleRefresh();
        }
        return data;
    } catch (err) {
        console.error('API Error:', err);
        throw err;
    }
}

// Debounced refresh to sync all tabs
let _refreshTimer = null;
function scheduleRefresh(){
    if(_refreshTimer) clearTimeout(_refreshTimer);
    _refreshTimer = setTimeout(refreshAll, 150);
}
async function refreshAll(){
    await loadDashboardData();
    // reload profile-dependent fields
    await loadProfileIntoSettings();
    // mark other tabs stale for future activation
    staleTabs = new Set(['tab-planner','tab-simulator','tab-progress','tab-sandbox','tab-transactions','tab-audit','tab-settings']);
    // if a stale tab is currently visible, reload it now
    const active = document.querySelector('.tab-pane.active');
    if(active){
        const id = active.id.replace('tab-','');
        if(staleTabs.has(active.id)){
            switchTab(active.id);
            staleTabs.delete(active.id);
        }
    }
}

// KPI click-to-edit handler (placeholder — extend as needed)
function openKpiEditModal(kpi) {
    // No-op stub; implement a KPI edit modal here if desired
    console.info('KPI card clicked:', kpi);
}

function switchTab(tabId) {
    document.querySelectorAll('.tab-pane').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));

    const targetTab = document.getElementById(tabId);
    if (targetTab) targetTab.classList.add('active');

    const targetNav = document.querySelector(`.nav-item[data-tab="${tabId}"]`);
    if (targetNav) targetNav.classList.add('active');

    if (tabId === 'tab-dashboard') loadDashboardData();
    if (tabId === 'tab-anomalies') runAnomalyScan();
    if (tabId === 'tab-transactions') loadTransactions();
    if (tabId === 'tab-sandbox') loadSandboxProposals();
    if (tabId === 'tab-tracker') loadProgressTracker();
    if (tabId === 'tab-audit') loadAuditLogs();
    if (tabId === 'tab-settings') loadProfileIntoSettings();

    lucide.createIcons();
}

function handleRoleChange(newRole) {
    currentRole = newRole;
    showToast(`Active permission tier switched to: ${newRole}`, 'info');
    loadDashboardData();
}

async function checkUserProfile() {
    try {
        currentProfile = await apiFetch('/api/profile');
        if (!currentProfile.onboarded) {
            openOnboardingWizard();
        }
    } catch (err) {
        console.error("Profile check error:", err);
    }
}

async function loadDashboardData() {
    try {
        const data = await apiFetch('/api/dashboard/summary');
        
        // Handle Setup banner & Low Data Banner
        const setupBanner = document.getElementById('dashboard-setup-banner');
        if (setupBanner) {
            setupBanner.style.display = data.needs_setup ? 'block' : 'none';
        }
        const lowDataBanner = document.getElementById('low-data-banner');
        if (lowDataBanner) {
            lowDataBanner.style.display = data.low_data && !data.needs_setup ? 'flex' : 'none';
        }

        document.getElementById('kpi-income').textContent = formatUSD(data.metrics.monthly_income);
        document.getElementById('kpi-expense').textContent = formatUSD(data.metrics.total_monthly_expense);
        document.getElementById('kpi-expense-breakdown').textContent = 
            `Needs: ${formatUSD(data.metrics.essential_expense)} | Wants: ${formatUSD(data.metrics.discretionary_expense)}`;
        
        document.getElementById('kpi-surplus').textContent = formatUSD(data.metrics.monthly_surplus);
        document.getElementById('kpi-savings-rate').textContent = `${data.metrics.savings_rate_pct}%`;
        
        const hs = data.health_score;
        document.getElementById('kpi-health-score').textContent = hs.overall_score;
        document.getElementById('kpi-health-rating').textContent = `${hs.rating}`;
        document.getElementById('kpi-health-grade').textContent = `Grade ${hs.grade}`;
        document.getElementById('health-bar-fill').style.width = `${hs.overall_score}%`;
        document.getElementById('health-ai-advice').textContent = hs.summary_recommendation;

        const hContainer = document.getElementById('health-breakdown-container');
        hContainer.innerHTML = '';
        
        const pillars = [
            { key: 'savings_ratio', name: 'Savings Rate Ratio', data: hs.breakdown.savings_ratio },
            { key: 'essential_adherence', name: 'Essential Needs (50% rule)', data: hs.breakdown.essential_adherence },
            { key: 'discretionary_control', name: 'Discretionary Wants Control', data: hs.breakdown.discretionary_control },
            { key: 'emergency_buffer', name: 'Emergency Runway Buffer', data: hs.breakdown.emergency_buffer }
        ];

        pillars.forEach(p => {
            const pct = (p.data.score / p.data.max) * 100;
            const el = document.createElement('div');
            el.className = 'health-pillar-item';
            el.innerHTML = `
                <div class="pillar-top">
                    <span class="pillar-name">${p.name}</span>
                    <span class="pillar-score">${p.data.score} / ${p.data.max} pts</span>
                </div>
                <div class="pillar-bar">
                    <div class="pillar-bar-fill" style="width: ${pct}%"></div>
                </div>
                <div class="pillar-desc">${p.data.detail}</div>
            `;
            hContainer.appendChild(el);
        });

        renderCashFlowChart(data.cash_flow_trends);
        renderCategoryDonutChart(data.category_breakdown);

        activeGoalList = data.goals;
        if (activeGoalList && activeGoalList.length > 0) _activeGoalIdForMC = activeGoalList[0].id;
        populateGoalSelectors(data.goals);
        renderDashboardGoals(data.goals);

        document.getElementById('anomaly-badge').textContent = data.metrics.anomaly_count;
        document.getElementById('sandbox-badge').textContent = data.metrics.pending_proposals_count;

        lucide.createIcons();
    } catch (err) {
        console.error("Dashboard load failed:", err);
    }
}

function renderDashboardGoals(goals) {
    const container = document.getElementById('dashboard-goals-container');
    container.innerHTML = '';

    if (!goals || goals.length === 0) {
        container.innerHTML = '<div class="text-muted" style="padding: 1rem; text-align: center;">No active goals yet. Click "+ Plan New Goal" to begin!</div>';
        return;
    }

    goals.forEach(g => {
        const pct = Math.min(100, (g.current_savings / g.target_amount) * 100).toFixed(1);
        const card = document.createElement('div');
        card.className = 'goal-card-item';
        card.innerHTML = `
            <div class="goal-top">
                <span class="goal-title">${g.title}</span>
                <span class="goal-progress-num">${pct}%</span>
            </div>
            <div class="goal-bar-wrap">
                <div class="goal-bar-fill" style="width: ${pct}%"></div>
            </div>
            <div class="goal-footer">
                <span>Saved: ${formatUSD(g.current_savings)} / ${formatUSD(g.target_amount)}</span>
                <span>Plan: <strong>${g.selected_plan || 'Balanced'}</strong></span>
            </div>
        `;
        container.appendChild(card);
    });
}

function populateGoalSelectors(goals) {
    const simSelect = document.getElementById('sim-goal-select');
    if (simSelect) {
        simSelect.innerHTML = '';
        if (goals.length === 0) {
            const opt = document.createElement('option');
            opt.value = "";
            opt.textContent = "No goals created yet";
            simSelect.appendChild(opt);
            return;
        }
        goals.forEach(g => {
            const opt = document.createElement('option');
            opt.value = g.id;
            opt.textContent = `${g.title} (${formatUSD(g.target_amount)})`;
            simSelect.appendChild(opt);
        });
    }
}

// Onboarding Wizard Logic
function openOnboardingWizard() {
    const nextYear = new Date();
    nextYear.setFullYear(nextYear.getFullYear() + 1);
    const dateInput = document.getElementById('ob-goal-date');
    if (dateInput) dateInput.value = nextYear.toISOString().split('T')[0];

    goToObStep(1);
    openModal('modal-onboarding');
}

function goToObStep(stepNum) {
    document.querySelectorAll('.ob-step').forEach(el => el.style.display = 'none');
    const step = document.getElementById(`ob-step-${stepNum}`);
    if (step) step.style.display = 'block';
}

async function handleOnboardingSubmit(e) {
    e.preventDefault();

    const income = parseFloat(document.getElementById('ob-income').value) || 0;
    const expense = parseFloat(document.getElementById('ob-expense').value) || 0;
    const goalTitle = document.getElementById('ob-goal-title').value;
    const goalTarget = parseFloat(document.getElementById('ob-goal-target').value) || 1000;
    const goalCurrent = parseFloat(document.getElementById('ob-goal-current').value) || 0;
    const goalDate = document.getElementById('ob-goal-date').value;
    const risk = document.getElementById('ob-risk').value;
    const emergency = parseInt(document.getElementById('ob-emergency').value) || 6;
    const inflation = parseFloat(document.getElementById('ob-inflation').value) || 0.03;

    try {
        // 1. Update Profile
        await apiFetch('/api/profile', {
            method: 'PUT',
            body: JSON.stringify({
                monthly_income: income,
                est_monthly_expense: expense,
                risk_profile: risk,
                emergency_months: emergency,
                inflation: inflation
            })
        });

        // 2. Create Initial Goal
        await apiFetch('/api/goals', {
            method: 'POST',
            body: JSON.stringify({
                title: goalTitle,
                target_amount: goalTarget,
                current_savings: goalCurrent,
                target_date: goalDate
            })
        });

        closeModal('modal-onboarding');
        showToast("Profile and initial goal configured!", "success");
        await loadDashboardData();
    } catch (err) {
        console.error("Onboarding error:", err);
    }
}

async function loadProfileIntoSettings() {
    try {
        currentProfile = await apiFetch('/api/profile');
        document.getElementById('prof-fullname').value = currentProfile.full_name || '';
        document.getElementById('prof-income').value = currentProfile.monthly_income || 0;
        document.getElementById('prof-expense').value = currentProfile.est_monthly_expense || 0;
        document.getElementById('prof-risk').value = currentProfile.risk_profile || 'moderate';
        document.getElementById('prof-emergency-months').value = currentProfile.emergency_months || 6;
        document.getElementById('prof-inflation').value = currentProfile.inflation || 0.03;
    } catch (err) {
        console.error("Load profile settings error:", err);
    }
}

async function handleSaveProfile(e) {
    e.preventDefault();

    const fullName = document.getElementById('prof-fullname').value;
    const income = parseFloat(document.getElementById('prof-income').value) || 0;
    const expense = parseFloat(document.getElementById('prof-expense').value) || 0;
    const risk = document.getElementById('prof-risk').value;
    const emergency = parseInt(document.getElementById('prof-emergency-months').value) || 6;
    const inflation = parseFloat(document.getElementById('prof-inflation').value) || 0.03;

    try {
        await apiFetch('/api/profile', {
            method: 'PUT',
            body: JSON.stringify({
                full_name: fullName,
                monthly_income: income,
                est_monthly_expense: expense,
                risk_profile: risk,
                emergency_months: emergency,
                inflation: inflation
            })
        });
        showToast("Profile settings saved!", "success");
        loadDashboardData();
    } catch (err) {
        console.error("Save profile error:", err);
    }
}

async function handleLoadDemoData() {
    if (!confirm("This will overwrite existing data with a realistic 12-month USD demo dataset. Proceed?")) return;
    try {
        await apiFetch('/api/admin/load-demo-data', { method: 'POST' });
        showToast("12-month demo dataset loaded successfully!", "success");
        loadDashboardData();
        closeModal('modal-onboarding');
    } catch (err) {
        console.error("Load demo data error:", err);
    }
}

async function handleClearAllData() {
    if (!confirm("Are you sure you want to clear all transactions and goals and reset your profile to blank?")) return;
    try {
        await apiFetch('/api/admin/clear-data', { method: 'POST' });
        showToast("All user data cleared.", "info");
        loadDashboardData();
    } catch (err) {
        console.error("Clear data error:", err);
    }
}

// Goal Planner
async function handleCalculatePlans(e) {
    e.preventDefault();

    const title = document.getElementById('goal-title').value;
    const target = parseFloat(document.getElementById('goal-target').value);
    const current = parseFloat(document.getElementById('goal-current').value);
    const deadline = document.getElementById('goal-date').value;

    const btn = document.getElementById('btn-generate-plans');
    btn.innerHTML = '<i data-lucide="loader"></i> Calculating AI Strategies...';
    btn.disabled = true;

    try {
        const res = await apiFetch('/api/goals/calculate-plans', {
            method: 'POST',
            body: JSON.stringify({
                title: title,
                target_amount: target,
                current_savings: current,
                target_date: deadline
            })
        });

        currentGeneratedPlans = res;
        renderGoalPlanResults(res);
        showToast("Generated 3 AI Financial Action Plans!", "success");
    } catch (err) {
        console.error("Plan generation error:", err);
    } finally {
        btn.innerHTML = '<i data-lucide="sparkles"></i> Generate 3 AI Action Plans';
        btn.disabled = false;
        lucide.createIcons();
    }
}

function renderGoalPlanResults(data) {
    const summaryBox = document.getElementById('planner-summary-content');
    summaryBox.innerHTML = `
        <div style="font-size: 0.88rem; line-height: 1.5; margin-bottom: 0.85rem;">
            ${data.summary_advice}
        </div>
        <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 0.6rem; text-align: center;">
            <div class="tracker-stat-pill">
                <span>Months Left</span>
                <strong>${data.months_remaining} mo</strong>
            </div>
            <div class="tracker-stat-pill">
                <span>Current Surplus</span>
                <strong>${formatUSD(data.current_monthly_surplus)}</strong>
            </div>
            <div class="tracker-stat-pill">
                <span>Required / mo</span>
                <strong class="text-cyan">${formatUSD(data.required_monthly_savings)}</strong>
            </div>
        </div>
    `;

    const plansContainer = document.getElementById('plans-container');
    plansContainer.style.display = 'grid';
    plansContainer.innerHTML = '';

    data.plans.forEach(p => {
        const isRec = p.plan_name === 'Balanced';
        const card = document.createElement('div');
        card.className = `plan-card ${isRec ? 'recommended' : ''}`;
        card.innerHTML = `
            ${isRec ? '<div class="plan-ribbon">AI RECOMMENDED</div>' : ''}
            <div class="plan-header">
                <div class="plan-name">${p.plan_name} Strategy</div>
                <div class="plan-feasibility">Feasibility: <strong>${p.feasibility}</strong></div>
            </div>
            <div class="plan-saving-stat">
                <div class="plan-target-amount">${formatUSD(p.monthly_savings_target)}</div>
                <div class="plan-subtext">Monthly Savings Target</div>
            </div>
            <div class="plan-stats-list">
                <div class="plan-stat-row">
                    <span>Goal Completion</span>
                    <strong>${p.months_to_complete} months</strong>
                </div>
                <div class="plan-stat-row">
                    <span>Spending Cut / mo</span>
                    <strong class="text-emerald">-${formatUSD(p.total_spending_cut)}</strong>
                </div>
                <div class="plan-stat-row">
                    <span>Status vs Deadline</span>
                    <strong class="${p.surplus_or_deficit === 'Surplus' ? 'text-emerald' : 'text-amber'}">${p.surplus_or_deficit}</strong>
                </div>
            </div>
            <div class="plan-advice">
                ${p.ai_recommendation}
            </div>
            <button class="btn btn-block ${isRec ? 'btn-primary' : 'btn-secondary'}" onclick="selectPlanAction('${p.plan_name}', ${p.monthly_savings_target})">
                ${isRec ? 'Select & Apply Plan' : 'Choose ' + p.plan_name}
            </button>
        `;
        plansContainer.appendChild(card);
    });

    const adjCard = document.getElementById('adjustments-detail-card');
    adjCard.style.display = 'block';
    const tbody = document.getElementById('adjustments-table-body');
    tbody.innerHTML = '';

    const balancedPlan = data.plans.find(p => p.plan_name === 'Balanced') || data.plans[0];
    balancedPlan.adjustments.forEach(adj => {
        const row = document.createElement('tr');
        row.innerHTML = `
            <td><strong>${adj.category}</strong></td>
            <td>${formatUSD(adj.current_spending)}</td>
            <td><span class="badge badge-warning">-${adj.cut_percentage}%</span></td>
            <td>${formatUSD(adj.recommended_spending)}</td>
            <td class="text-emerald"><strong>+${formatUSD(adj.monthly_saved)}</strong></td>
            <td class="text-muted" style="font-size: 0.76rem;">${adj.rationale}</td>
        `;
        tbody.appendChild(row);
    });

    lucide.createIcons();

    // Wire MC goal ID and prefill params from profile
    if (data.goal_id) {
        _activeGoalIdForMC = data.goal_id;
        if (currentProfile) {
            document.getElementById('mc-input-return').value = currentProfile.expected_return ?? 0.05;
            document.getElementById('mc-input-vol').value   = currentProfile.volatility ?? 0.10;
            document.getElementById('mc-input-inf').value   = currentProfile.inflation ?? 0.03;
        }
        document.getElementById('quant-risk-card').style.display = '';
    }
}

async function selectPlanAction(planName, monthlyContrib) {
    if (activeGoalList.length === 0) {
        showToast("No active goal found.", "warning");
        return;
    }
    const goalId = activeGoalList[0].id;
    try {
        await apiFetch(`/api/goals/${goalId}/select-plan`, {
            method: 'POST',
            body: JSON.stringify({
                plan_name: planName,
                monthly_contribution: monthlyContrib
            })
        });
        showToast(`Applied ${planName} plan to ${activeGoalList[0].title}!`, "success");
        loadDashboardData();
    } catch (err) {
        console.error("Select plan error:", err);
    }
}

async function stageProposalFromPlan() {
    if (!currentGeneratedPlans) {
        showToast("Please calculate a plan first.", "warning");
        return;
    }
    const balanced = currentGeneratedPlans.plans.find(p => p.plan_name === 'Balanced');
    const targetGoalId = activeGoalList.length > 0 ? activeGoalList[0].id : 1;

    try {
        await apiFetch('/api/sandbox/proposals', {
            method: 'POST',
            body: JSON.stringify({
                title: `AI Budget Optimization for ${currentGeneratedPlans.title}`,
                action_type: 'BUDGET_REALLOCATION',
                description: `Trim non-essential expenses to save ${formatUSD(balanced.total_spending_cut)}/month.`,
                impact_summary: `Increases monthly savings by +${formatUSD(balanced.total_spending_cut)}/mo | Achieves target in ${balanced.months_to_complete} months`,
                projected_savings_delta: balanced.total_spending_cut,
                payload: {
                    goal_id: targetGoalId,
                    plan_name: 'Balanced',
                    adjustments: balanced.adjustments
                }
            })
        });
        showToast("Proposal staged in Sandbox! Go to Sandbox Approvals tab.", "success");
        switchTab('tab-sandbox');
    } catch (err) {
        console.error("Stage proposal error:", err);
    }
}

// Anomaly Scanning
async function runAnomalyScan() {
    try {
        const res = await apiFetch('/api/transactions/scan-anomalies', { method: 'POST' });
        document.getElementById('anomaly-scanned-count').textContent = res.total_scanned;
        document.getElementById('anomaly-detected-count').textContent = res.anomalies_detected;
        document.getElementById('anomaly-timestamp').textContent = `Last scan: ${res.scan_timestamp}`;

        const container = document.getElementById('anomalies-list-container');
        container.innerHTML = '';

        if (res.anomalies.length === 0) {
            container.innerHTML = '<div class="text-muted" style="padding: 2rem; text-align: center;">No irregular spending anomalies detected. All expenses match typical statistical baseline.</div>';
            return;
        }

        res.anomalies.forEach(a => {
            const el = document.createElement('div');
            el.className = 'anomaly-item-card';
            el.innerHTML = `
                <div class="anomaly-item-left">
                    <div class="anomaly-alert-icon"><i data-lucide="alert-triangle"></i></div>
                    <div>
                        <div class="anomaly-title">${a.description} (${a.category})</div>
                        <div class="anomaly-desc">${a.reason}</div>
                        <div style="margin-top: 0.4rem; display: flex; gap: 0.5rem; align-items: center;">
                            <span class="anomaly-stat-badge">Z: +${a.z_score}</span>
                            <span class="badge ${a.severity === 'HIGH' ? 'badge-danger' : 'badge-warning'}">${a.severity} SEVERITY</span>
                            <span class="text-muted" style="font-size: 0.72rem;">Typical Category Avg: ${formatUSD(a.moving_average)}</span>
                        </div>
                    </div>
                </div>
                <div style="text-align: right; flex-shrink: 0;">
                    <div class="anomaly-amount">${formatUSD(a.amount)}</div>
                    <span class="text-muted" style="font-size: 0.72rem;">${a.date}</span>
                </div>
            `;
            container.appendChild(el);
        });

        lucide.createIcons();
    } catch (err) {
        console.error("Anomaly scan error:", err);
    }
}

// Simulator
function updateSimFormLabels() {
    const type = document.getElementById('sim-type').value;
    const label = document.getElementById('sim-amount-label');
    if (type === 'one_off_purchase') label.textContent = 'Purchase Amount ($)';
    if (type === 'recurring_expense_change') label.textContent = 'Monthly Expense Change ($)';
    if (type === 'income_change') label.textContent = 'Monthly Income Change ($)';
}

function setSimPreset(type, desc, amount) {
    document.getElementById('sim-type').value = type;
    document.getElementById('sim-desc').value = desc;
    document.getElementById('sim-amount').value = amount;
    updateSimFormLabels();
}

async function handleRunSimulation(e) {
    e.preventDefault();

    const type = document.getElementById('sim-type').value;
    const desc = document.getElementById('sim-desc').value;
    const amount = parseFloat(document.getElementById('sim-amount').value);
    const goalVal = document.getElementById('sim-goal-select').value;
    const goalId = goalVal ? parseInt(goalVal) : null;

    try {
        const res = await apiFetch('/api/simulate', {
            method: 'POST',
            body: JSON.stringify({
                scenario_type: type,
                description: desc,
                amount: amount,
                goal_id: goalId
            })
        });

        renderSimulationResult(res);
        showToast("Simulation computed successfully!", "success");
    } catch (err) {
        console.error("Sim error:", err);
    }
}

function renderSimulationResult(res) {
    const tag = document.getElementById('sim-impact-tag');
    tag.textContent = res.impact_level;
    tag.className = `card-badge ${res.impact_level === 'SEVERE' ? 'badge-danger' : res.impact_level === 'BENEFICIAL' ? 'badge-success' : 'badge-warning'}`;

    const container = document.getElementById('sim-results-container');
    container.innerHTML = `
        <div class="sim-impact-grid">
            <div class="sim-metric-box">
                <span class="sim-metric-label">Savings Rate Shift</span>
                <div class="sim-metric-val ${res.new_savings_rate >= res.original_savings_rate ? 'text-emerald' : 'text-rose'}">
                    ${res.new_savings_rate}%
                </div>
                <div class="sim-metric-delta text-muted">Original: ${res.original_savings_rate}%</div>
            </div>
            <div class="sim-metric-box">
                <span class="sim-metric-label">Monthly Surplus Delta</span>
                <div class="sim-metric-val ${res.new_monthly_surplus >= res.original_monthly_surplus ? 'text-emerald' : 'text-rose'}">
                    ${formatUSD(res.new_monthly_surplus)}
                </div>
                <div class="sim-metric-delta text-muted">Original: ${formatUSD(res.original_monthly_surplus)}</div>
            </div>
            <div class="sim-metric-box">
                <span class="sim-metric-label">Timeline to Reach Goal</span>
                <div class="sim-metric-val">${res.new_completion_months ? res.new_completion_months + ' mo' : 'Unachievable'}</div>
                <div class="sim-metric-delta text-muted">Original: ${res.original_completion_months || 'N/A'} mo</div>
            </div>
            <div class="sim-metric-box">
                <span class="sim-metric-label">Goal Milestone Impact</span>
                <div class="sim-metric-val ${res.delay_or_advance_months > 0 ? 'text-rose' : 'text-emerald'}">
                    ${res.delay_or_advance_months > 0 ? '+' + res.delay_or_advance_months + ' mo Delay' : Math.abs(res.delay_or_advance_months) + ' mo Earlier'}
                </div>
                <div class="sim-metric-delta text-muted">Impact Score: ${res.impact_level}</div>
            </div>
        </div>
        <div class="ai-insight-box">
            <i data-lucide="info"></i>
            <div class="insight-text">${res.recommendation}</div>
        </div>
    `;

    lucide.createIcons();
}

// Progress Tracker
async function loadProgressTracker() {
    if (activeGoalList.length === 0) {
        document.getElementById('tracker-goals-list').innerHTML = 
            '<div class="text-muted" style="padding: 2rem; text-align: center;">No goals available. Plan a goal to start tracking progress.</div>';
        return;
    }
    const container = document.getElementById('tracker-goals-list');
    container.innerHTML = '';

    for (const g of activeGoalList) {
        try {
            const p = await apiFetch(`/api/goals/${g.id}/progress`);
            const el = document.createElement('div');
            el.className = 'tracker-item-card';
            el.innerHTML = `
                <div class="tracker-header">
                    <strong style="font-size: 1rem;">${p.title}</strong>
                    <span class="badge ${p.on_track ? 'badge-success' : 'badge-danger'}">${p.status_label}</span>
                </div>
                <div class="goal-bar-wrap">
                    <div class="goal-bar-fill" style="width: ${p.completion_percentage}%"></div>
                </div>
                <div class="tracker-stats-row">
                    <div class="tracker-stat-pill">
                        <span>Current Saved</span>
                        <strong>${formatUSD(p.current_savings)}</strong>
                    </div>
                    <div class="tracker-stat-pill">
                        <span>Expected to Date</span>
                        <strong>${formatUSD(p.expected_savings_to_date)}</strong>
                    </div>
                    <div class="tracker-stat-pill">
                        <span>Target Gap</span>
                        <strong class="${p.actual_vs_expected_gap >= 0 ? 'text-emerald' : 'text-rose'}">
                            ${p.actual_vs_expected_gap >= 0 ? '+' : ''}${formatUSD(p.actual_vs_expected_gap)}
                        </strong>
                    </div>
                </div>
                <div class="ai-insight-box" style="margin-top: 0.6rem;">
                    <i data-lucide="compass"></i>
                    <div class="insight-text">${p.actionable_advice}</div>
                </div>
            `;
            container.appendChild(el);
        } catch (err) {
            console.error("Tracker fetch error:", err);
        }
    }

    renderTrajectoryChart(activeGoalList[0]);
    lucide.createIcons();
}

// Sandbox
async function loadSandboxProposals() {
    try {
        const proposals = await apiFetch('/api/sandbox/proposals');
        const container = document.getElementById('sandbox-proposals-container');
        container.innerHTML = '';

        if (proposals.length === 0) {
            container.innerHTML = '<div class="text-muted" style="padding: 2rem; text-align: center;">No proposals currently in queue.</div>';
            return;
        }

        proposals.forEach(p => {
            const isPending = p.status === 'PENDING';
            const el = document.createElement('div');
            el.className = 'proposal-item';
            el.innerHTML = `
                <div class="proposal-top">
                    <div>
                        <div class="proposal-title">${p.title}</div>
                        <span class="badge ${isPending ? 'badge-warning' : p.status === 'APPROVED' ? 'badge-success' : 'badge-danger'}">
                            ${p.status}
                        </span>
                        <span class="text-muted" style="font-size: 0.72rem; margin-left: 0.5rem;">Action: ${p.action_type}</span>
                    </div>
                    <span class="text-muted" style="font-size: 0.72rem;">${p.proposed_at}</span>
                </div>
                <div class="proposal-body">${p.description}</div>
                <div class="proposal-impact"><i data-lucide="zap"></i> ${p.impact_summary}</div>
                ${isPending ? `
                    <div class="proposal-actions">
                        <button class="btn btn-sm btn-secondary" onclick="handleProposalAction(${p.id}, 'REJECT')">
                            <i data-lucide="x"></i> Reject
                        </button>
                        <button class="btn btn-sm btn-success" onclick="handleProposalAction(${p.id}, 'APPROVE')">
                            <i data-lucide="check"></i> Authorize & Apply
                        </button>
                    </div>
                ` : `
                    <div class="text-muted" style="font-size: 0.74rem; text-align: right;">
                        Resolved at ${p.resolved_at} by role: <strong>${p.resolved_by_role || 'User'}</strong>
                    </div>
                `}
            `;
            container.appendChild(el);
        });

        lucide.createIcons();
    } catch (err) {
        console.error("Sandbox load error:", err);
    }
}

async function handleProposalAction(proposalId, action) {
    try {
        await apiFetch(`/api/sandbox/proposals/${proposalId}/action`, {
            method: 'POST',
            body: JSON.stringify({ action: action })
        });
        showToast(`Proposal #${proposalId} was ${action.toLowerCase()}d!`, "success");
        loadSandboxProposals();
        loadDashboardData();
    } catch (err) {
        console.error("Proposal action error:", err);
    }
}

// Transactions
async function loadTransactions() {
    const typeFilter = document.getElementById('tx-filter-type').value;
    const anomalyFilter = document.getElementById('tx-filter-anomaly').checked;

    let url = `/api/transactions?`;
    if (typeFilter) url += `type=${typeFilter}&`;
    if (anomalyFilter) url += `anomaly_only=true&`;

    try {
        const txs = await apiFetch(url);
        const tbody = document.getElementById('transactions-table-body');
        tbody.innerHTML = '';

        if (txs.length === 0) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="8" style="text-align: center; padding: 2rem; color: var(--text-muted);">
                        No transactions recorded yet. Click <strong>"+ Add Transaction"</strong> to add your first entry!
                    </td>
                </tr>
            `;
            return;
        }

        txs.forEach(t => {
            const row = document.createElement('tr');
            row.innerHTML = `
                <td>${t.date}</td>
                <td><span class="badge ${t.type === 'income' ? 'badge-success' : 'badge-warning'}">${t.type.toUpperCase()}</span></td>
                <td><strong>${t.category}</strong></td>
                <td>${t.description}</td>
                <td style="font-family: var(--font-mono); font-weight: 700;" class="${t.type === 'income' ? 'text-emerald' : ''}">${formatUSD(t.amount)}</td>
                <td>${t.is_essential ? '<span class="badge badge-info">Need</span>' : '<span class="badge badge-viewer">Want</span>'}</td>
                <td>
                    ${t.is_anomaly ? `<span class="badge badge-danger" title="${t.anomaly_reason || ''}"><i data-lucide="alert-triangle"></i> ANOMALY</span>` : '<span class="text-muted">Normal</span>'}
                </td>
                <td>
                    <button class="btn btn-sm btn-secondary" onclick="handleDeleteTransaction(${t.id})" title="Delete Transaction">
                        <i data-lucide="trash-2"></i>
                    </button>
                </td>
            `;
            tbody.appendChild(row);
        });

        lucide.createIcons();
    } catch (err) {
        console.error("Load txs error:", err);
    }
}

async function handleDeleteTransaction(txId) {
    if (!confirm(`Are you sure you want to delete transaction #${txId}?`)) return;
    try {
        await apiFetch(`/api/transactions/${txId}`, { method: 'DELETE' });
        showToast(`Transaction #${txId} deleted.`, "info");
        loadTransactions();
        loadDashboardData();
    } catch (err) {
        console.error("Delete tx error:", err);
    }
}

async function loadAuditLogs() {
    try {
        const logs = await apiFetch('/api/audit/logs');
        const tbody = document.getElementById('audit-table-body');
        tbody.innerHTML = '';

        logs.forEach(l => {
            const row = document.createElement('tr');
            row.innerHTML = `
                <td style="font-family: var(--font-mono); font-size: 0.74rem;">${l.timestamp}</td>
                <td><span class="badge ${l.user_role === 'Admin' ? 'badge-admin' : l.user_role === 'User' ? 'badge-user' : 'badge-viewer'}">${l.user_role}</span></td>
                <td><strong>${l.action_type}</strong></td>
                <td>${l.resource}</td>
                <td><span class="badge ${l.status === 'SUCCESS' || l.status === 'APPROVED' ? 'badge-success' : 'badge-danger'}">${l.status}</span></td>
                <td style="font-size: 0.75rem;">${l.details}</td>
                <td style="font-family: var(--font-mono); font-size: 0.72rem; color: var(--text-muted);">${l.ip_address}</td>
            `;
            tbody.appendChild(row);
        });

        lucide.createIcons();
    } catch (err) {
        console.error("Audit log error:", err);
    }
}

// Modal Handlers
function openModal(id) {
    const el = document.getElementById(id);
    if (el) el.classList.add('active');
}

function closeModal(id) {
    const el = document.getElementById(id);
    if (el) el.classList.remove('active');
}

function openAddTransactionModal() {
    document.getElementById('new-tx-date').value = new Date().toISOString().split('T')[0];
    updateCategoryDropdown();
    openModal('modal-add-tx');
}

function openAddCategoryModal() {
    openModal('modal-add-category');
}

function openNewProposalModal() {
    openModal('modal-new-proposal');
}

async function fetchCategories() {
    try {
        allCategories = await apiFetch('/api/transactions/categories');
    } catch (err) {
        console.error("Categories fetch error:", err);
    }
}

function updateCategoryDropdown() {
    const type = document.getElementById('new-tx-type').value;
    const catSelect = document.getElementById('new-tx-category');
    catSelect.innerHTML = '';

    const filtered = allCategories.filter(c => c.type === type);
    filtered.forEach(c => {
        const opt = document.createElement('option');
        opt.value = c.name;
        opt.textContent = c.name;
        catSelect.appendChild(opt);
    });
}

async function handleAddCategory(e) {
    e.preventDefault();

    const name = document.getElementById('cat-name').value.trim();
    const type = document.getElementById('cat-type').value;
    const isEss = document.getElementById('cat-essential').value === '1';

    try {
        await apiFetch('/api/transactions/categories', {
            method: 'POST',
            body: JSON.stringify({
                name: name,
                type: type,
                is_essential: isEss
            })
        });

        closeModal('modal-add-category');
        showToast(`Category '${name}' created!`, "success");
        await fetchCategories();
        updateCategoryDropdown();
    } catch (err) {
        console.error("Add category error:", err);
    }
}

async function handleAddTransaction(e) {
    e.preventDefault();

    const type = document.getElementById('new-tx-type').value;
    const cat = document.getElementById('new-tx-category').value;
    const amount = parseFloat(document.getElementById('new-tx-amount').value);
    const desc = document.getElementById('new-tx-desc').value;
    const date = document.getElementById('new-tx-date').value;

    try {
        await apiFetch('/api/transactions', {
            method: 'POST',
            body: JSON.stringify({
                category: cat,
                amount: amount,
                type: type,
                description: desc,
                date: date
            })
        });

        closeModal('modal-add-tx');
        showToast("Transaction recorded!", "success");
        loadTransactions();
        loadDashboardData();
    } catch (err) {
        console.error("Add tx error:", err);
    }
}

async function handleCreateCustomProposal(e) {
    e.preventDefault();

    const title = document.getElementById('prop-title').value;
    const actionType = document.getElementById('prop-action-type').value;
    const desc = document.getElementById('prop-desc').value;
    const delta = parseFloat(document.getElementById('prop-savings-delta').value);
    const targetGoalId = activeGoalList.length > 0 ? activeGoalList[0].id : 1;

    try {
        await apiFetch('/api/sandbox/proposals', {
            method: 'POST',
            body: JSON.stringify({
                title: title,
                action_type: actionType,
                description: desc,
                impact_summary: `Monthly savings increase +${formatUSD(delta)}/mo`,
                projected_savings_delta: delta,
                payload: { goal_id: targetGoalId, custom: true }
            })
        });

        closeModal('modal-new-proposal');
        showToast("Proposal staged in Sandbox!", "success");
        loadSandboxProposals();
    } catch (err) {
        console.error("Create proposal error:", err);
    }
}

// ── Quant: Monte Carlo Fan Chart ────────────────────────────────────────────
let _activeGoalIdForMC = null;

async function handleRunGoalMonteCarlo(e) {
    e.preventDefault();
    if (!_activeGoalIdForMC) { showToast('Save a goal first to run risk analysis.', 'warning'); return; }
    const ret = parseFloat(document.getElementById('mc-input-return').value);
    const vol = parseFloat(document.getElementById('mc-input-vol').value);
    const inf = parseFloat(document.getElementById('mc-input-inf').value);
    try {
        const data = await apiFetch(
            `/api/quant/goal/${_activeGoalIdForMC}/monte-carlo?return=${ret}&vol=${vol}&inflation=${inf}`
        );
        if (data.needs_setup) { showToast('Complete profile setup first.', 'warning'); return; }

        // Stats
        const prob = (data.prob_success * 100).toFixed(1);
        document.getElementById('mc-prob-badge').textContent = `Probability: ${prob}%`;
        document.getElementById('mc-stat-prob').textContent = `${prob}%`;
        document.getElementById('mc-stat-prob').style.color = prob >= 70 ? 'var(--emerald)' : prob >= 40 ? 'var(--amber)' : 'var(--rose)';
        document.getElementById('mc-stat-real-target').textContent = formatUSD(data.real_target);
        document.getElementById('mc-stat-pmt').textContent = `${formatUSD(data.required_monthly_pmt)}/mo`;

        // Low-data banner
        const card = document.getElementById('quant-risk-card');
        let bannerEl = card.querySelector('.low-data-banner');
        if (data.low_data) {
            if (!bannerEl) {
                bannerEl = document.createElement('div');
                bannerEl.className = 'low-data-banner';
                bannerEl.style.cssText = 'background:rgba(245,158,11,0.15);border:1px solid var(--amber);border-radius:6px;padding:0.5rem 0.75rem;font-size:0.78rem;color:var(--amber);margin-bottom:0.75rem;display:flex;align-items:center;gap:0.4rem;';
                bannerEl.innerHTML = '<i data-lucide="alert-triangle" style="width:14px;height:14px;"></i><span>Limited transaction history — estimates based on declared monthly expense.</span>';
                card.querySelector('.dashboard-grid').before(bannerEl);
                lucide.createIcons();
            }
        } else if (bannerEl) { bannerEl.remove(); }

        // Fan chart (P10/P50/P90)
        const pts = data.paths_percentiles || [];
        const labels = pts.map(p => `M${p.month}`);
        const p10 = pts.map(p => p.p10);
        const p50 = pts.map(p => p.p50);
        const p90 = pts.map(p => p.p90);

        const ctx = document.getElementById('mcFanChart').getContext('2d');
        if (mcFanChartInstance) mcFanChartInstance.destroy();
        mcFanChartInstance = new Chart(ctx, {
            type: 'line',
            data: {
                labels,
                datasets: [
                    { label: 'P90 (Optimistic)', data: p90, borderColor: '#10b981', backgroundColor: 'rgba(16,185,129,0.08)', fill: '+1', tension: 0.35, pointRadius: 0, borderWidth: 1.5 },
                    { label: 'P50 (Median)',     data: p50, borderColor: '#6366f1', backgroundColor: 'rgba(99,102,241,0.12)', fill: '+1', tension: 0.35, pointRadius: 0, borderWidth: 2 },
                    { label: 'P10 (Pessimistic)',data: p10, borderColor: '#f43f5e', backgroundColor: 'rgba(244,63,94,0.06)',  fill: false, tension: 0.35, pointRadius: 0, borderWidth: 1.5 },
                ]
            },
            options: {
                responsive: true, maintainAspectRatio: false,
                plugins: { legend: { labels: { color: '#94a3b8', font: { size: 11 } } }, tooltip: { callbacks: { label: c => `${c.dataset.label}: ${formatUSD(c.raw)}` } } },
                scales: {
                    x: { ticks: { color: '#64748b', maxTicksLimit: 8 }, grid: { color: 'rgba(255,255,255,0.04)' } },
                    y: { ticks: { color: '#64748b', callback: v => formatUSD(v) }, grid: { color: 'rgba(255,255,255,0.04)' } }
                }
            }
        });
        document.getElementById('quant-risk-card').style.display = '';
        showToast(`Monte Carlo: ${prob}% chance of success`, prob >= 70 ? 'success' : 'warning');
    } catch (err) { console.error('MC error:', err); }
}

// ── Quant: Loan vs Cash ──────────────────────────────────────────────────────
async function handleLoanVsCashSubmit(e) {
    e.preventDefault();
    const body = {
        price:              parseFloat(document.getElementById('lvc-price').value),
        down_payment:       parseFloat(document.getElementById('lvc-down').value),
        annual_rate:        parseFloat(document.getElementById('lvc-rate').value),
        months:             parseInt(document.getElementById('lvc-months').value),
        opportunity_return: parseFloat(document.getElementById('lvc-opp-return').value) || null,
    };
    try {
        const d = await apiFetch('/api/quant/loan-vs-cash', { method: 'POST', body: JSON.stringify(body) });
        const box = document.getElementById('lvc-result-box');
        const betterColor = d.better === 'loan' ? 'var(--emerald)' : 'var(--cyan)';
        const badge = document.getElementById('loan-vs-cash-badge');
        badge.textContent = d.better === 'loan' ? '✓ Loan Cheaper (NPV)' : '✓ Cash Cheaper';
        badge.style.background = d.better === 'loan' ? 'rgba(16,185,129,0.2)' : 'rgba(6,182,212,0.2)';
        box.innerHTML = `
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:0.6rem;font-size:0.83rem;">
                <div class="tracker-stat-pill"><span>Monthly Payment</span><strong>${formatUSD(d.monthly_payment)}</strong></div>
                <div class="tracker-stat-pill"><span>Total Interest</span><strong style="color:var(--rose);">${formatUSD(d.total_interest)}</strong></div>
                <div class="tracker-stat-pill"><span>NPV Cost (Loan)</span><strong>${formatUSD(d.npv_cost_loan)}</strong></div>
                <div class="tracker-stat-pill"><span>NPV Cost (Cash)</span><strong>${formatUSD(d.npv_cost_cash)}</strong></div>
            </div>
            <div style="margin-top:0.75rem;padding:0.6rem;background:rgba(0,0,0,0.2);border-radius:6px;text-align:center;font-size:0.85rem;">
                <strong style="color:${betterColor};font-size:1rem;">👉 ${d.better === 'loan' ? 'Take the Loan' : 'Pay Cash'}</strong>
                <div style="color:#64748b;font-size:0.75rem;margin-top:0.2rem;">Lower NPV opportunity cost</div>
            </div>`;
        lucide.createIcons();
    } catch (err) { console.error('LvC error:', err); }
}

document.addEventListener('DOMContentLoaded', async () => {
    document.querySelectorAll('.nav-item').forEach(btn => {
        btn.addEventListener('click', () => {
            const tab = btn.getAttribute('data-tab');
            switchTab(tab);
        });
    });

    const goalDateInput = document.getElementById('goal-date');
    if (goalDateInput) {
        const nextYear = new Date();
        nextYear.setFullYear(nextYear.getFullYear() + 1);
        goalDateInput.value = nextYear.toISOString().split('T')[0];
    }

    await fetchCategories();
    await checkUserProfile();
    await loadDashboardData();
    lucide.createIcons();
});
