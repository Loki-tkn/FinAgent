/**
 * Chart.js configurations for AI Personal Financial Agent (USD & Quant Fan Chart)
 */

let cashFlowChartInstance = null;
let categoryDonutChartInstance = null;
let trajectoryChartInstance = null;
let mcFanChartInstance = null;

function formatUSD(value) {
    return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }).format(value);
}

function renderCashFlowChart(trendData) {
    const ctx = document.getElementById('cashFlowChart');
    if (!ctx) return;

    if (cashFlowChartInstance) {
        cashFlowChartInstance.destroy();
    }

    const labels = trendData.map(d => d.month);
    const incomeData = trendData.map(d => d.income);
    const expenseData = trendData.map(d => d.expense);
    const netSavings = trendData.map(d => d.net_savings);

    cashFlowChartInstance = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: labels,
            datasets: [
                {
                    label: 'Income ($)',
                    data: incomeData,
                    backgroundColor: 'rgba(16, 185, 129, 0.75)',
                    borderColor: '#10b981',
                    borderWidth: 1,
                    borderRadius: 6,
                    barPercentage: 0.6
                },
                {
                    label: 'Expenses ($)',
                    data: expenseData,
                    backgroundColor: 'rgba(244, 63, 94, 0.75)',
                    borderColor: '#f43f5e',
                    borderWidth: 1,
                    borderRadius: 6,
                    barPercentage: 0.6
                },
                {
                    label: 'Net Surplus ($)',
                    data: netSavings,
                    type: 'line',
                    borderColor: '#06b6d4',
                    backgroundColor: 'rgba(6, 182, 212, 0.1)',
                    borderWidth: 3,
                    fill: false,
                    tension: 0.3,
                    pointBackgroundColor: '#06b6d4',
                    pointRadius: 4
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    labels: {
                        color: '#9ca3af',
                        font: { family: "'Plus Jakarta Sans', sans-serif", size: 11 }
                    }
                },
                tooltip: {
                    callbacks: {
                        label: function (context) {
                            return `${context.dataset.label}: ${formatUSD(context.raw)}`;
                        }
                    }
                }
            },
            scales: {
                x: {
                    grid: { color: 'rgba(255, 255, 255, 0.05)' },
                    ticks: { color: '#9ca3af', font: { family: "'JetBrains Mono', monospace", size: 10 } }
                },
                y: {
                    grid: { color: 'rgba(255, 255, 255, 0.05)' },
                    ticks: {
                        color: '#9ca3af',
                        font: { family: "'JetBrains Mono', monospace", size: 10 },
                        callback: function (val) {
                            return '$' + val.toLocaleString();
                        }
                    }
                }
            }
        }
    });
}

function renderCategoryDonutChart(categories) {
    const ctx = document.getElementById('categoryDonutChart');
    if (!ctx) return;

    if (categoryDonutChartInstance) {
        categoryDonutChartInstance.destroy();
    }

    const labels = categories.map(c => c.category);
    const data = categories.map(c => c.amount);

    const colorPalette = [
        '#06b6d4', '#3b82f6', '#8b5cf6', '#ec4899', 
        '#f43f5e', '#f59e0b', '#10b981', '#14b8a6', '#6366f1'
    ];

    categoryDonutChartInstance = new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: labels,
            datasets: [{
                data: data,
                backgroundColor: colorPalette.slice(0, labels.length),
                borderColor: '#162032',
                borderWidth: 2,
                hoverOffset: 6
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            cutout: '68%',
            plugins: {
                legend: {
                    position: 'bottom',
                    labels: {
                        color: '#9ca3af',
                        boxWidth: 12,
                        padding: 10,
                        font: { family: "'Plus Jakarta Sans', sans-serif", size: 10 }
                    }
                },
                tooltip: {
                    callbacks: {
                        label: function (context) {
                            const total = context.dataset.data.reduce((a, b) => a + b, 0);
                            const val = context.raw;
                            const pct = total > 0 ? ((val / total) * 100).toFixed(1) : 0;
                            return `${context.label}: ${formatUSD(val)} (${pct}%)`;
                        }
                    }
                }
            }
        }
    });
}

function renderTrajectoryChart(goal) {
    const ctx = document.getElementById('trajectoryChart');
    if (!ctx) return;

    if (trajectoryChartInstance) {
        trajectoryChartInstance.destroy();
    }

    const totalTarget = goal.target_amount || 18000;
    const currentSavings = goal.current_savings || 6500;
    const monthlyContrib = goal.monthly_contribution || 800;

    const months = ['Month 0', 'M+2', 'M+4', 'M+6', 'M+8', 'M+10', 'M+12', 'M+14'];
    const targetLine = months.map((_, i) => Math.min(totalTarget, (totalTarget / 12) * (i * 2)));
    const actualLine = months.map((_, i) => Math.min(totalTarget, currentSavings + (monthlyContrib * i * 2)));

    trajectoryChartInstance = new Chart(ctx, {
        type: 'line',
        data: {
            labels: months,
            datasets: [
                {
                    label: 'Target Trajectory ($)',
                    data: targetLine,
                    borderColor: 'rgba(255, 255, 255, 0.3)',
                    borderDash: [5, 5],
                    borderWidth: 2,
                    fill: false,
                    pointRadius: 0
                },
                {
                    label: 'Projected / Actual Savings ($)',
                    data: actualLine,
                    borderColor: '#10b981',
                    backgroundColor: 'rgba(16, 185, 129, 0.12)',
                    borderWidth: 3,
                    fill: true,
                    tension: 0.2,
                    pointBackgroundColor: '#10b981',
                    pointRadius: 4
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    labels: { color: '#9ca3af', font: { family: "'Plus Jakarta Sans', sans-serif", size: 11 } }
                },
                tooltip: {
                    callbacks: {
                        label: function (context) {
                            return `${context.dataset.label}: ${formatUSD(context.raw)}`;
                        }
                    }
                }
            },
            scales: {
                x: {
                    grid: { color: 'rgba(255, 255, 255, 0.05)' },
                    ticks: { color: '#9ca3af', font: { family: "'JetBrains Mono', monospace", size: 10 } }
                },
                y: {
                    grid: { color: 'rgba(255, 255, 255, 0.05)' },
                    ticks: {
                        color: '#9ca3af',
                        font: { family: "'JetBrains Mono', monospace", size: 10 },
                        callback: function (val) {
                            return '$' + val.toLocaleString();
                        }
                    }
                }
            }
        }
    });
}

function renderMonteCarloFanChart(pathsPercentiles, realTarget) {
    const ctx = document.getElementById('mcFanChart');
    if (!ctx) return;

    if (mcFanChartInstance) {
        mcFanChartInstance.destroy();
    }

    const labels = pathsPercentiles.map(p => `M${p.month}`);
    const p90 = pathsPercentiles.map(p => p.p90);
    const p50 = pathsPercentiles.map(p => p.p50);
    const p10 = pathsPercentiles.map(p => p.p10);
    const targetLine = pathsPercentiles.map(() => realTarget);

    mcFanChartInstance = new Chart(ctx, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [
                {
                    label: 'P90 (Optimistic $)',
                    data: p90,
                    borderColor: '#10b981',
                    backgroundColor: 'rgba(16, 185, 129, 0.1)',
                    borderWidth: 2,
                    fill: '+1',
                    pointRadius: 0,
                    tension: 0.2
                },
                {
                    label: 'P50 (Median Expected $)',
                    data: p50,
                    borderColor: '#06b6d4',
                    backgroundColor: 'rgba(6, 182, 212, 0.15)',
                    borderWidth: 3,
                    fill: '+1',
                    pointRadius: 2,
                    tension: 0.2
                },
                {
                    label: 'P10 (Pessimistic $)',
                    data: p10,
                    borderColor: '#f59e0b',
                    backgroundColor: 'transparent',
                    borderWidth: 2,
                    fill: false,
                    pointRadius: 0,
                    tension: 0.2
                },
                {
                    label: 'Inflation-Adjusted Target ($)',
                    data: targetLine,
                    borderColor: 'rgba(244, 63, 94, 0.8)',
                    borderDash: [6, 4],
                    borderWidth: 2,
                    fill: false,
                    pointRadius: 0
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    position: 'bottom',
                    labels: { color: '#9ca3af', font: { family: "'Plus Jakarta Sans', sans-serif", size: 10 } }
                },
                tooltip: {
                    callbacks: {
                        label: function (context) {
                            return `${context.dataset.label}: ${formatUSD(context.raw)}`;
                        }
                    }
                }
            },
            scales: {
                x: {
                    grid: { color: 'rgba(255, 255, 255, 0.05)' },
                    ticks: { color: '#9ca3af', font: { family: "'JetBrains Mono', monospace", size: 10 } }
                },
                y: {
                    grid: { color: 'rgba(255, 255, 255, 0.05)' },
                    ticks: {
                        color: '#9ca3af',
                        font: { family: "'JetBrains Mono', monospace", size: 10 },
                        callback: function (val) {
                            return '$' + val.toLocaleString();
                        }
                    }
                }
            }
        }
    });
}
